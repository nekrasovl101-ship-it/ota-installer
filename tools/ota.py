#!/usr/bin/env python3
"""
OTA Installer — сервер.

  python3 tools/ota.py add            # залить все IPA из папки ipa_inbox/
  python3 tools/ota.py add file.ipa   # залить конкретный IPA
  python3 tools/ota.py list           # что сейчас на сервере
  python3 tools/ota.py remove <bundle id>

Флаги для add:  --no-upload  (не лить в GitHub, только обновить каталог локально)
                --no-push    (не делать git push)
"""
import argparse
import datetime
import json
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.parse
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
INBOX = ROOT / "ipa_inbox"
CATALOG = DOCS / "apps.json"
CONFIG = ROOT / "config.json"


# ---------- утилиты ----------

def load_config():
    cfg = json.loads(CONFIG.read_text("utf-8"))
    cfg.setdefault("release_tag", "ipas")
    cfg["repo_full"] = f'{cfg["github_user"]}/{cfg["repo"]}'
    cfg["site_url"] = cfg.get("site_url") or f'https://{cfg["github_user"]}.github.io/{cfg["repo"]}'
    cfg["site_url"] = cfg["site_url"].rstrip("/")
    return cfg


def load_catalog(cfg):
    if CATALOG.exists():
        return json.loads(CATALOG.read_text("utf-8"))
    return {"name": cfg.get("store_name", "OTA Installer"), "apps": []}


def save_catalog(cat):
    cat["updated"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    cat["apps"].sort(key=lambda a: a.get("added", ""), reverse=True)
    CATALOG.write_text(json.dumps(cat, ensure_ascii=False, indent=2) + "\n", "utf-8")


def run(cmd, check=True, **kw):
    print("  $", " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT, check=check, **kw)


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9._-]", "_", s)


def human_size(n):
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024


# ---------- PNG из IPA (Apple "CgBI" -> обычный PNG) ----------

def _chunk(ctype, body):
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF)


def fix_apple_png(data):
    """Иконки в IPA часто пожаты Xcode'ом в формат CgBI, который браузер/iOS-манифест не покажут.
    Переводим в нормальный PNG (BGRA -> RGBA, нормальный zlib)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return data
    pos, chunks = 8, []
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        chunks.append((ctype, data[pos + 8:pos + 8 + length]))
        pos += 12 + length
        if ctype == b"IEND":
            break
    if not any(c == b"CgBI" for c, _ in chunks):
        return data
    ihdr = next(b for c, b in chunks if c == b"IHDR")
    w, h, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", ihdr[:13])
    if depth != 8 or color not in (2, 6) or interlace:
        return None
    raw = bytearray(zlib.decompressobj(-15).decompress(b"".join(b for c, b in chunks if c == b"IDAT")))
    bpp = 4 if color == 6 else 3
    stride = w * bpp + 1
    # Фильтры PNG работают по каналам независимо, так что можно менять B и R прямо в отфильтрованных данных
    for y in range(h):
        s = y * stride + 1
        e = s + w * bpp
        raw[s:e:bpp], raw[s + 2:e:bpp] = raw[s + 2:e:bpp], raw[s:e:bpp]
    out = b"\x89PNG\r\n\x1a\n"
    idat_done = False
    for c, b in chunks:
        if c == b"CgBI":
            continue
        if c == b"IDAT":
            if not idat_done:
                out += _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
                idat_done = True
            continue
        out += _chunk(c, b)
    return out


# ---------- разбор IPA ----------

def read_ipa(path):
    z = zipfile.ZipFile(path)
    names = z.namelist()
    app_dir = None
    for n in names:
        m = re.match(r"^Payload/[^/]+\.app/Info\.plist$", n)
        if m:
            app_dir = n[: -len("Info.plist")]
            break
    if not app_dir:
        raise ValueError("нет Payload/*.app/Info.plist — это точно IPA?")
    info = plistlib.loads(z.read(app_dir + "Info.plist"))

    meta = {
        "id": info.get("CFBundleIdentifier", ""),
        "name": info.get("CFBundleDisplayName") or info.get("CFBundleName") or Path(path).stem,
        "version": str(info.get("CFBundleShortVersionString", "1.0")),
        "build": str(info.get("CFBundleVersion", "1")),
        "minOS": str(info.get("MinimumOSVersion", "")),
        "size": Path(path).stat().st_size,
    }
    if not meta["id"]:
        raise ValueError("в Info.plist нет CFBundleIdentifier")

    # --- подпись / профиль ---
    top = {n[len(app_dir):]: n for n in names if n.startswith(app_dir) and "/" not in n[len(app_dir):].rstrip("/")}
    meta["signed"] = (app_dir + "_CodeSignature/CodeResources") in names
    if "embedded.mobileprovision" in top:
        blob = z.read(top["embedded.mobileprovision"])
        a, b = blob.find(b"<?xml"), blob.find(b"</plist>")
        if a != -1 and b != -1:
            try:
                prof = plistlib.loads(blob[a:b + 8])
                exp = prof.get("ExpirationDate")
                meta["expires"] = exp.strftime("%Y-%m-%d") if exp else None
                meta["team"] = prof.get("TeamName")
                meta["profileType"] = (
                    "enterprise" if prof.get("ProvisionsAllDevices")
                    else "adhoc/dev" if prof.get("ProvisionedDevices") else "appstore"
                )
            except Exception:
                pass

    # --- иконка ---
    bases = set()
    for key in ("CFBundleIcons", "CFBundleIcons~ipad"):
        prim = (info.get(key) or {}).get("CFBundlePrimaryIcon") or {}
        if isinstance(prim, dict):
            bases.update(prim.get("CFBundleIconFiles") or [])
            if prim.get("CFBundleIconName"):
                bases.add(prim["CFBundleIconName"])
    bases.update(info.get("CFBundleIconFiles") or [])
    if info.get("CFBundleIconFile"):
        bases.add(info["CFBundleIconFile"])
    bases.add("AppIcon")
    cands = [
        n for short, n in top.items()
        if short.lower().endswith(".png") and any(short.startswith(Path(b).stem) for b in bases)
    ]
    icon = None
    for n in sorted(cands, key=lambda n: z.getinfo(n).file_size, reverse=True):
        fixed = fix_apple_png(z.read(n))
        if fixed:
            icon = fixed
            break
    return meta, icon


# ---------- GitHub ----------

def need_gh():
    if not shutil.which("gh"):
        sys.exit("✖ Нет GitHub CLI. Поставь:  brew install gh  &&  gh auth login")


def upload_ipa(cfg, ipa_path, asset):
    need_gh()
    repo, tag = cfg["repo_full"], cfg["release_tag"]
    if run(["gh", "release", "view", tag, "-R", repo], check=False, capture_output=True).returncode != 0:
        run(["gh", "release", "create", tag, "-R", repo, "-t", "IPA", "-n", "Хранилище IPA для OTA Installer"])
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / asset
        shutil.copyfile(ipa_path, dst)
        run(["gh", "release", "upload", tag, str(dst), "-R", repo, "--clobber"])
    return f"https://github.com/{repo}/releases/download/{tag}/{asset}"


def git_push(msg):
    if not (ROOT / ".git").exists():
        print("  (git не настроен — запусти ./setup.sh, пушить нечего)")
        return
    run(["git", "add", "docs"])
    if run(["git", "diff", "--cached", "--quiet"], check=False).returncode == 0:
        print("  (изменений нет)")
        return
    run(["git", "commit", "-q", "-m", msg])
    run(["git", "push", "-q"])


# ---------- команды ----------

def make_manifest(cfg, app):
    assets = [{"kind": "software-package", "url": app["ipa"]}]
    icon_url = f'{cfg["site_url"]}/{app["icon"]}'
    assets += [{"kind": "display-image", "url": icon_url}, {"kind": "full-size-image", "url": icon_url}]
    return {"items": [{
        "assets": assets,
        "metadata": {
            "bundle-identifier": app["id"],
            "bundle-version": app["version"],
            "kind": "software",
            "title": app["name"],
        },
    }]}


def cmd_add(args):
    cfg = load_config()
    files = [Path(p) for p in args.files] or sorted(INBOX.glob("*.ipa"))
    if not files:
        sys.exit(f"Нечего заливать: кинь .ipa в {INBOX.relative_to(ROOT)}/ или передай путь.")
    cat = load_catalog(cfg)
    added = []
    for f in files:
        print(f"\n▶ {f.name}")
        try:
            meta, icon = read_ipa(f)
        except Exception as e:
            print(f"  ✖ пропускаю: {e}")
            continue
        if not meta["signed"]:
            print("  ⚠ IPA НЕ подписан — по OTA он не встанет. Подпиши и залей заново.")
            if not args.force:
                continue
        if meta.get("expires"):
            print(f'  профиль: {meta.get("profileType")}, команда {meta.get("team")}, до {meta["expires"]}')

        key = safe_name(meta["id"])
        icon_rel = "icons/default.png"
        if icon:
            (DOCS / "icons" / f"{key}.png").write_bytes(icon)
            icon_rel = f"icons/{key}.png"
        else:
            print("  (иконку не нашёл — ставлю дефолтную)")

        asset = f"{key}.ipa"
        if args.no_upload:
            ipa_url = f'https://github.com/{cfg["repo_full"]}/releases/download/{cfg["release_tag"]}/{asset}'
        else:
            ipa_url = upload_ipa(cfg, f, asset)

        old = next((a for a in cat["apps"] if a["id"] == meta["id"]), None)
        app = {
            **meta,
            "sizeText": human_size(meta["size"]),
            "icon": icon_rel,
            "ipa": ipa_url,
            "manifest": f"manifests/{key}.plist",
            "added": (old or {}).get("added") or datetime.date.today().isoformat(),
            "updatedAt": datetime.date.today().isoformat(),
        }
        manifest_url = f'{cfg["site_url"]}/{app["manifest"]}'
        app["installURL"] = "itms-services://?action=download-manifest&url=" + urllib.parse.quote(manifest_url, safe="")
        (DOCS / app["manifest"]).write_bytes(plistlib.dumps(make_manifest(cfg, app)))

        cat["apps"] = [a for a in cat["apps"] if a["id"] != meta["id"]] + [app]
        added.append(f'{app["name"]} {app["version"]}')
        print(f'  ✔ {app["name"]} {app["version"]} ({app["build"]}) — {app["sizeText"]}')

        if f.parent.resolve() == INBOX.resolve():
            done = INBOX / "done"
            done.mkdir(exist_ok=True)
            f.rename(done / f.name)

    if not added:
        sys.exit("\nНичего не добавлено.")
    save_catalog(cat)
    if not args.no_push:
        git_push("Добавлено: " + ", ".join(added))
    print(f'\nГотово. Сайт: {cfg["site_url"]}/  (GitHub Pages обновляется ~1 мин)')


def cmd_list(_):
    cfg = load_config()
    cat = load_catalog(cfg)
    if not cat["apps"]:
        print("Пусто.")
    for a in cat["apps"]:
        exp = f', профиль до {a["expires"]}' if a.get("expires") else ""
        print(f'• {a["name"]} {a["version"]} ({a["build"]})  {a["id"]}  {a["sizeText"]}{exp}')


def cmd_remove(args):
    cfg = load_config()
    cat = load_catalog(cfg)
    app = next((a for a in cat["apps"] if a["id"] == args.bundle_id), None)
    if not app:
        sys.exit(f"Нет такого: {args.bundle_id}")
    key = safe_name(app["id"])
    cat["apps"] = [a for a in cat["apps"] if a["id"] != app["id"]]
    for p in (DOCS / app["manifest"], DOCS / "icons" / f"{key}.png"):
        if p.exists():
            p.unlink()
    save_catalog(cat)
    if not args.no_upload:
        need_gh()
        run(["gh", "release", "delete-asset", cfg["release_tag"], f"{key}.ipa", "-R", cfg["repo_full"], "-y"], check=False)
    if not args.no_push:
        git_push(f'Удалено: {app["name"]}')
    print(f'✔ {app["name"]} убран')


def main():
    p = argparse.ArgumentParser(description="OTA Installer — сервер")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="залить IPA")
    a.add_argument("files", nargs="*")
    a.add_argument("--no-upload", action="store_true")
    a.add_argument("--no-push", action="store_true")
    a.add_argument("--force", action="store_true", help="залить даже неподписанный")
    a.set_defaults(fn=cmd_add)
    sub.add_parser("list", help="список").set_defaults(fn=cmd_list)
    r = sub.add_parser("remove", help="убрать приложение")
    r.add_argument("bundle_id")
    r.add_argument("--no-upload", action="store_true")
    r.add_argument("--no-push", action="store_true")
    r.set_defaults(fn=cmd_remove)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
