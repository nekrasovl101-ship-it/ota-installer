#!/bin/bash
# Включает автозаливку: кинул IPA в ipa_inbox — он сам уезжает на сервер.
cd "$(dirname "$0")"
DIR="$(pwd)"
PLIST="$HOME/Library/LaunchAgents/com.leva.otainstaller.plist"
chmod +x tools/autoupload.sh
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.leva.otainstaller</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$DIR/tools/autoupload.sh</string></array>
  <key>WatchPaths</key><array><string>$DIR/ipa_inbox</string></array>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/tmp/ota-installer.out</string>
  <key>StandardErrorPath</key><string>/tmp/ota-installer.out</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/com.leva.otainstaller" 2>/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST"
osascript -e 'display notification "Кидай IPA в ipa_inbox — зальётся сам" with title "AppCore: автозаливка включена"'
echo "✔ Автозаливка включена. Работает в фоне, даже после перезагрузки."
echo "Если macOS спросит доступ к «Рабочему столу» для bash/python3 — жми «Разрешить»."
echo "Лог: server/autoupload.log"
echo
read -n 1 -s -r -p "Нажми любую клавишу, чтобы закрыть"
