#!/bin/bash
# Собирает быструю команду, подписывает её и выкладывает на сервер.
cd "$(dirname "$0")"
TMP=$(mktemp -d)
python3 tools/make_shortcut.py "$TMP/OTA Installer.shortcut" >/dev/null || exit 1
echo "Подписываю команду (нужен вход в iCloud на Маке)..."
shortcuts sign --mode anyone --input "$TMP/OTA Installer.shortcut" --output "docs/OTA-Installer.shortcut" || { echo "✖ Не получилось подписать"; read -n1; exit 1; }
cp "docs/OTA-Installer.shortcut" "../OTA Installer.shortcut"
git add -A && git commit -q -m "Быстрая команда" && git push -q
USER_=$(python3 -c 'import json;print(json.load(open("config.json"))["github_user"])')
REPO=$(python3 -c 'import json;print(json.load(open("config.json"))["repo"])')
echo
echo "✔ Команда готова."
echo "Открой на айфоне в Safari (через ~1 мин):"
echo "   https://$USER_.github.io/$REPO/OTA-Installer.shortcut"
echo "и нажми «Добавить быструю команду». Или скинь файл «OTA Installer.shortcut» по AirDrop."
echo
read -n 1 -s -r -p "Нажми любую клавишу, чтобы закрыть"
