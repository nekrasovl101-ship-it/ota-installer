#!/usr/bin/env python3
"""Запускается на серверах GitHub Actions.

  python3 tools/ci.py sign    # подписать IPA выбранным сертификатом (env APP, CERT, REQ)
  python3 tools/ci.py certs   # проверить все сертификаты на отзыв (OCSP Apple)
"""
import base64
import datetime
import io
import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
CFG = json.loads((ROOT / "config.json").read_text("utf-8"))
REPO = f'{CFG["github_user"]}/{CFG["repo"]}'
SITE = (CFG.get("site_url") or f'https://{CFG["github_user"]}.github.io/{CFG["repo"]}').rstrip("/")
TAG = CFG.get("release_tag", "ipas")
AS_IS = "📦 Как есть (без переподписи)"


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def sh(cmd, **kw):
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def secrets():
    return json.loads(os.environ.get("ALL_SECRETS") or "{}")


def unpack_cert(b64, dst: Path):
    """Секрет CERT_xxx = base64(zip: cert.p12, cert.mobileprovision, password.txt)."""
    dst.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(b64))) as z:
        z.extractall(dst)
    pw = (dst / "password.txt").read_text("utf-8").strip() if (dst / "password.txt").exists() else ""
    return dst / "cert.p12", dst / "cert.mobileprovision", pw


def read_profile(path: Path):
    blob = path.read_bytes()
    a, b = blob.find(b"<?xml"), blob.find(b"</plist>")
    return plistlib.loads(blob[a:b + 8])


def p12_to_pem(p12: Path, pw: str, out: Path):
    for extra in (["-legacy"], []):
        r = subprocess.run(["openssl", "pkcs12", "-in", str(p12), "-clcerts", "-nokeys", "-passin", f"pass:{pw}",
                            "-out", str(out)] + extra, capture_output=True)
        if r.returncode == 0 and out.exists() and out.stat().st_size:
            return True
    return False


def x509(pem: Path, *args):
    return subprocess.run(["openssl", "x509", "-in", str(pem), "-noout", *args],
                          capture_output=True, text=True).stdout.strip()


def ocsp_status(pem: Path, work: Path):
    """good / revoked / unknown"""
    url = x509(pem, "-ocsp_uri")
    text = x509(pem, "-text")
    m = re.search(r"CA Issuers - URI:(\S+)", text)
    if not url or not m:
        return "unknown"
    try:
        der = urllib.request.urlopen(m.group(1), timeout=20).read()
        (work / "issuer.der").write_bytes(der)
        subprocess.run(["openssl", "x509", "-inform", "DER", "-in", str(work / "issuer.der"),
                        "-out", str(work / "issuer.pem")], check=True, capture_output=True)
        r = subprocess.run(["openssl", "ocsp", "-issuer", str(work / "issuer.pem"), "-cert", str(pem),
                            "-url", url, "-noverify"], capture_output=True, text=True, timeout=30)
        out = r.stdout + r.stderr
        if ": revoked" in out:
            return "revoked"
        if ": good" in out:
            return "good"
    except Exception as e:
        print("OCSP ошибка:", e)
    return "unknown"


def git_push(msg):
    sh(["git", "config", "user.name", "ota-bot"])
    sh(["git", "config", "user.email", "ota-bot@users.noreply.github.com"])
    sh(["git", "add", "docs"])
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        print("изменений нет")
        return
    sh(["git", "commit", "-q", "-m", msg])
    for i in range(6):
        if subprocess.run(["git", "pull", "-q", "--rebase"]).returncode == 0 and \
                subprocess.run(["git", "push", "-q"]).returncode == 0:
            return
        time.sleep(3 + i * 2)
    sys.exit("не смог запушить")


# ---------------- подпись ----------------

def write_job(req, **data):
    (DOCS / "jobs").mkdir(parents=True, exist_ok=True)
    (DOCS / "jobs" / f"{req}.json").write_text(json.dumps({"time": now(), **data}, ensure_ascii=False), "utf-8")


def cmd_sign():
    app_key, cert_id, req = os.environ["APP"], os.environ["CERT"], os.environ["REQ"]
    for v in (app_key, cert_id, req):
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", v):
            sys.exit(f"плохой параметр: {v!r}")
    try:
        _sign(app_key, cert_id, req)
    except BaseException as e:
        msg = str(e) or e.__class__.__name__
        print("ОШИБКА:", msg)
        write_job(req, status="error", message=msg[:300])
        git_push(f"Подпись не удалась: {app_key}")
        sys.exit(1)


def _sign(app_key, cert_id, req):
    cat = json.loads((DOCS / "apps.json").read_text("utf-8"))
    app = next((a for a in cat["apps"] if re.sub(r"[^A-Za-z0-9._-]", "_", a["id"]) == app_key), None)
    if not app:
        raise RuntimeError("приложение не найдено на сервере")
    b64 = secrets().get(f"CERT_{cert_id}")
    if not b64:
        raise RuntimeError("сертификат не найден (нет секрета)")

    work = Path(tempfile.mkdtemp())
    p12, prov, pw = unpack_cert(b64, work / "cert")
    profile = read_profile(prov)
    exp = profile.get("ExpirationDate")
    if exp and exp.replace(tzinfo=datetime.timezone.utc) < datetime.datetime.now(datetime.timezone.utc):
        raise RuntimeError("профиль сертификата истёк")

    # если профиль не wildcard — меняем bundle id на тот, что в профиле
    app_id = (profile.get("Entitlements") or {}).get("application-identifier", "")
    bundle_id = app["id"]
    extra = []
    if app_id and not app_id.endswith("*"):
        bundle_id = app_id.split(".", 1)[1]
        extra = ["-b", bundle_id]

    sh(["gh", "release", "download", TAG, "-R", REPO, "-p", f"{app_key}.ipa", "-D", str(work), "--clobber"])
    out = work / "signed.ipa"
    r = subprocess.run(["zsign", "-k", str(p12), "-p", pw, "-m", str(prov), "-o", str(out), "-z", "6", *extra,
                        str(work / f"{app_key}.ipa")], capture_output=True, text=True)
    print(r.stdout[-3000:], r.stderr[-3000:])
    if r.returncode != 0 or not out.exists():
        raise RuntimeError("zsign не смог подписать (неверный пароль или битый сертификат?)")

    asset = f"{app_key}__{cert_id}.ipa"
    (work / asset).write_bytes(out.read_bytes())
    sh(["gh", "release", "upload", TAG, str(work / asset), "-R", REPO, "--clobber"])
    ipa_url = f"https://github.com/{REPO}/releases/download/{TAG}/{asset}"

    icon_url = f'{SITE}/{app.get("icon", "icons/default.png")}'
    manifest = {"items": [{
        "assets": [{"kind": "software-package", "url": ipa_url},
                   {"kind": "display-image", "url": icon_url},
                   {"kind": "full-size-image", "url": icon_url}],
        "metadata": {"bundle-identifier": bundle_id, "bundle-version": app["version"],
                     "kind": "software", "title": app["name"]},
    }]}
    (DOCS / "signed").mkdir(parents=True, exist_ok=True)
    rel = f"signed/{app_key}__{cert_id}.plist"
    (DOCS / rel).write_bytes(plistlib.dumps(manifest))
    install = "itms-services://?action=download-manifest&url=" + urllib.parse.quote(f"{SITE}/{rel}", safe="")
    write_job(req, status="done", installURL=install, app=app["name"], cert=cert_id)
    git_push(f"Подписано: {app['name']} → {cert_id}")


# ---------------- проверка сертификатов ----------------

def cmd_certs():
    path = DOCS / "certs.json"
    data = json.loads(path.read_text("utf-8")) if path.exists() else {"certs": []}
    known = {c["id"]: c for c in data.get("certs", [])}
    sec = secrets()
    today = datetime.datetime.now(datetime.timezone.utc)
    certs = []
    for key in sorted(k for k in sec if k.startswith("CERT_")):
        cid = key[5:]
        c = dict(known.get(cid, {"id": cid}))
        work = Path(tempfile.mkdtemp())
        try:
            p12, prov, pw = unpack_cert(sec[key], work)
            prof = read_profile(prov)
            pexp = prof.get("ExpirationDate")
            c.setdefault("name", prof.get("TeamName") or prof.get("Name") or cid)
            c["team"] = prof.get("TeamName")
            c["profileExpires"] = pexp.strftime("%Y-%m-%d") if pexp else None
            c["type"] = "enterprise" if prof.get("ProvisionsAllDevices") else "dev/adhoc"
            pem = work / "cert.pem"
            if not p12_to_pem(p12, pw, pem):
                c["status"] = "badpassword"
            else:
                na = x509(pem, "-enddate").split("=", 1)[-1]
                try:
                    cexp = datetime.datetime.strptime(na, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=datetime.timezone.utc)
                    c["certExpires"] = cexp.strftime("%Y-%m-%d")
                except ValueError:
                    cexp = None
                c["status"] = ocsp_status(pem, work)
                if c["status"] != "revoked" and ((cexp and cexp < today) or
                                                 (pexp and pexp.replace(tzinfo=datetime.timezone.utc) < today)):
                    c["status"] = "expired"
        except Exception as e:
            print(cid, "ошибка:", e)
            c["status"] = "broken"
        c["checked"] = now()
        print(f'{cid}: {c.get("name")} — {c["status"]}')
        certs.append(c)

    menu, ids = [AS_IS], {}
    for c in certs:
        if c["status"] in ("good", "unknown"):  # отозванные/истёкшие/битые прячем
            label = c.get("name") or c["id"]
            if c.get("profileExpires"):
                label += f' (до {c["profileExpires"]})'
            if label in ids:
                label += f' [{c["id"]}]'
            menu.append(label)
            ids[label] = c["id"]
    new = {"certs": certs, "menu": menu, "ids": ids, "asIs": AS_IS}
    old = {k: v for k, v in data.items() if k != "updated"}
    strip = lambda d: json.dumps({**d, "certs": [{k: v for k, v in c.items() if k != "checked"} for c in d.get("certs", [])]},
                                 sort_keys=True, ensure_ascii=False)
    if strip(new) == strip(old):
        print("ничего не поменялось")
        return
    new["updated"] = now()
    path.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", "utf-8")
    git_push("Статус сертификатов")


if __name__ == "__main__":
    {"sign": cmd_sign, "certs": cmd_certs}[sys.argv[1]]()
