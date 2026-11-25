#!/bin/bash
# Запускается macOS (launchd), когда в ipa_inbox что-то меняется.
export GH_PROMPT_DISABLED=1 PYTHONUNBUFFERED=1
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
cd "$(dirname "$0")/.." || exit 0
LOG="autoupload.log"
LOCK="/tmp/ota-installer.lock"

ls ipa_inbox >/dev/null 2>&1 || {
  osascript -e 'display notification "Нет доступа к Рабочему столу. Открой «Включить автозаливку.command» ещё раз" with title "OTA Installer ✖"'
  exit 0
}
ls ipa_inbox/*.ipa >/dev/null 2>&1 || exit 0
# зависший замок старше 3 часов — сносим
[ -d "$LOCK" ] && [ -n "$(find "$LOCK" -maxdepth 0 -mmin +180 2>/dev/null)" ] && rmdir "$LOCK"
mkdir "$LOCK" 2>/dev/null || exit 0          # уже заливает другой запуск
trap 'rmdir "$LOCK"' EXIT

# ждём, пока файлы докопируются (размер перестанет меняться)
prev=""
while :; do
  cur=$(ls -l ipa_inbox/*.ipa 2>/dev/null | awk '{s+=$5} END {print s}')
  [ -n "$cur" ] && [ "$cur" = "$prev" ] && break
  prev="$cur"; sleep 5
done

{
  echo "===== $(date '+%d.%m %H:%M:%S')"
  python3 -u tools/ota.py add --notify
} >>"$LOG" 2>&1

# пока заливали, могли докинуть ещё
ls ipa_inbox/*.ipa >/dev/null 2>&1 && { rmdir "$LOCK"; trap - EXIT; exec "$0"; }
