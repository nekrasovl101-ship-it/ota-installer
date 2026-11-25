#!/usr/bin/env python3
"""GitHub Actions: подпись IPA, которые залили люди через сайт.

  python3 tools/ci_upload.py sign      # env REQ, PARTS, CERT
  python3 tools/ci_upload.py cleanup   # удалить файлы старше 2 часов

Файлы лежат в приватном репо (config.json → uploads_repo), релиз «uploads».
Результат отправляется обратно в Cloudflare Worker (config.json → worker_url).
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
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402

CFG = ci.CFG
UP = CFG.get("uploads_repo") or f'{CFG["github_user"]}/ota-uploads'
WORKER = (CFG.get("worker_url") or "").rstrip("/")
TAG = "uploads"


def callback(req, data):
    r = urllib.request.Request(f"{WORKER}/done/{req}", data=json.dumps(data).encode(), method="POST",
                               headers={"content-type": "application/json", "x-key": os.environ["CALLBACK_KEY"],
                                        "user-agent": "ota-installer"})
    print("callback:", urllib.request.urlopen(r, timeout=30).read()[:200])


def app_info(ipa: Path):
    z = zipfile.ZipFile(ipa)
    info = next((n for n in z.namelist() if re.match(r"^Payload/[^/]+\.app/Info\.plist$", n)), None)
    if not info:
        raise RuntimeError("это не IPA (нет Payload/*.app)")
    d = plistlib.loads(z.read(info))
    return {"name": d.get("CFBundleDisplayName") or d.get("CFBundleName") or "App",
            "version": str(d.get("CFBundleShortVersionString", "1.0")),
            "bundle": d.get("CFBundleIdentifier", "")}


def cmd_sign():
    req, parts, cert = os.environ["REQ"], os.environ["PARTS"], os.environ["CERT"]
    if not re.fullmatch(r"[a-f0-9]{20}", req) or not re.fullmatch(r"\d{1,2}", parts) \
            or not re.fullmatch(r"[A-F0-9]{8}|OWN", cert):
        sys.exit("плохие параметры")
    try:
        _sign(req, int(parts), cert)
    except BaseException as e:
        msg = str(e) or e.__class__.__name__
        print("ОШИБКА:", msg)
        try:
            callback(req, {"status": "error", "message": msg[:300]})
        finally:
            sys.exit(1)


def own_cert(req, work: Path):
    """Свой сертификат человека: скачать, сразу удалить с GitHub, проверить пароль и отзыв."""
    name = f"{req}.cert"
    ci.sh(["gh", "release", "download", TAG, "-R", UP, "-p", name, "-D", str(work), "--clobber"])
    subprocess.run(["gh", "release", "delete-asset", TAG, name, "-R", UP, "-y"])
    d = json.loads((work / name).read_text())
    (work / name).unlink()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("cert.p12", base64.b64decode(d["p12"]))
        z.writestr("cert.mobileprovision", base64.b64decode(d["prov"]))
        z.writestr("password.txt", d.get("pw", ""))
    b64 = base64.b64encode(buf.getvalue()).decode()
    chk = work / "chk"
    p12, prov, pw = ci.unpack_cert(b64, chk)
    pem = chk / "cert.pem"
    if not ci.p12_to_pem(p12, pw, pem):
        raise RuntimeError("неверный пароль от сертификата (или битый .p12)")
    try:
        ci.read_profile(prov)
    except Exception:
        raise RuntimeError("битый .mobileprovision")
    if ci.ocsp_status(pem, chk) == "revoked":
        raise RuntimeError("твой сертификат отозван Apple — нужен другой")
    return b64


def _sign(req, parts, cert):
    work = Path(tempfile.mkdtemp())
    if cert == "OWN":
        b64 = own_cert(req, work)
    else:
        b64 = ci.secrets().get(f"CERT_{cert}")
    if not b64:
        raise RuntimeError("сертификат не найден")
    names = [f"{req}.part{i:02d}" for i in range(parts)]
    for n in names:
        ci.sh(["gh", "release", "download", TAG, "-R", UP, "-p", n, "-D", str(work), "--clobber"])
    src = work / "in.ipa"
    with open(src, "wb") as out:
        for n in names:
            out.write((work / n).read_bytes())
            (work / n).unlink()
    for n in names:  # кусочки больше не нужны
        subprocess.run(["gh", "release", "delete-asset", TAG, n, "-R", UP, "-y"])

    info = app_info(src)
    dst = work / f"{req}.ipa"
    info["bundle"] = ci.zsign_with(b64, src, dst, info["bundle"])
    ci.sh(["gh", "release", "upload", TAG, str(dst), "-R", UP, "--clobber"])
    asset = subprocess.run(["gh", "api", f"repos/{UP}/releases/tags/{TAG}", "--jq",
                            f'.assets[] | select(.name=="{req}.ipa") | .id'],
                           capture_output=True, text=True, check=True).stdout.strip()
    if not asset:
        raise RuntimeError("не нашёл подписанный файл после заливки")
    callback(req, {"status": "done", "asset": int(asset), **info})


def cmd_cleanup():
    out = subprocess.run(["gh", "api", "--paginate", f"repos/{UP}/releases/tags/{TAG}",
                          "--jq", ".assets[] | [.name, .created_at] | @tsv"],
                         capture_output=True, text=True, check=True).stdout
    now = datetime.datetime.now(datetime.timezone.utc)
    for line in out.splitlines():
        name, created = line.split("\t")
        age = now - datetime.datetime.fromisoformat(created.replace("Z", "+00:00"))
        # чужие сертификаты не держим дольше 15 минут, файлы — 2 часа
        if age > (datetime.timedelta(minutes=15) if name.endswith(".cert") else datetime.timedelta(hours=2)):
            print("удаляю", name)
            subprocess.run(["gh", "release", "delete-asset", TAG, name, "-R", UP, "-y"])


if __name__ == "__main__":
    {"sign": cmd_sign, "cleanup": cmd_cleanup}[sys.argv[1]]()
