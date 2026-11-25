#!/bin/bash
# Собирает быструю команду (с выбором сертификата), подписывает и добавляет в «Команды» на Маке —
# через iCloud она сама появится на айфоне.
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
USER_=$(python3 -c 'import json;print(json.load(open("config.json"))["github_user"])')
REPO=$(python3 -c 'import json;print(json.load(open("config.json"))["repo"])')

# 1. права на workflows (один раз)
if ! gh auth status 2>&1 | grep -q "workflow"; then
  echo "Нужно разрешить gh обновлять GitHub Actions. Сейчас откроется вход, как в прошлый раз (Enter → код → Authorize)."
  gh auth refresh -h github.com -s workflow || { read -n1; exit 1; }
fi

# 2. залить серверную часть (workflows, скрипты)
mkdir -p .github/workflows && cp tools/workflows/*.yml .github/workflows/
git add -A && git commit -q -m "Облачная подпись" 2>/dev/null
git rm -q --cached docs/OTA-Installer.shortcut 2>/dev/null && rm -f docs/OTA-Installer.shortcut && git commit -q -m "убрал старую команду"
git pull -q --rebase --autostash; git push -q || { echo "✖ не запушилось"; read -n1; exit 1; }

# 3. токен для команды
if [ ! -s .token ]; then
  echo
  echo "Нужен токен GitHub для команды (чтобы она могла запускать подпись)."
  echo "Сейчас откроется страница. Там:"
  echo "  • Token name: OTA Installer"
  echo "  • Expiration: максимальный срок"
  echo "  • Repository access: Only select repositories → $REPO"
  echo "  • Permissions → Repository permissions → Actions: Read and write"
  echo "  • Generate token → скопируй его (начинается с github_pat_)"
  open "https://github.com/settings/personal-access-tokens/new"
  echo
  read -r -p "Вставь токен сюда и нажми Enter: " TOKEN
  [ -z "$TOKEN" ] && { echo "Пусто — выхожу"; read -n1; exit 1; }
  printf '%s' "$TOKEN" > .token && chmod 600 .token
fi

# 4. собрать, подписать, добавить в «Команды»
TMP=$(mktemp -d)
python3 tools/make_shortcut.py "$TMP/OTA Installer.shortcut" >/dev/null || { read -n1; exit 1; }
shortcuts sign --mode people-who-know-me --input "$TMP/OTA Installer.shortcut" --output "$TMP/OTA Installer signed.shortcut" 2>/dev/null \
 || shortcuts sign --mode anyone --input "$TMP/OTA Installer.shortcut" --output "$TMP/OTA Installer signed.shortcut" \
 || { echo "✖ Не получилось подписать команду"; read -n1; exit 1; }
cp "$TMP/OTA Installer signed.shortcut" "../OTA Installer.shortcut"
open "../OTA Installer.shortcut"

echo
echo "✔ Готово. На Маке откроются «Команды» → нажми «Добавить» (если там уже есть старая — замени)."
echo "Через iCloud команда появится на айфоне. Если нет — скинь файл «OTA Installer.shortcut» по AirDrop."
echo "⚠ Файл команды содержит твой токен — никому не кидай."
echo
read -n 1 -s -r -p "Нажми любую клавишу, чтобы закрыть"
