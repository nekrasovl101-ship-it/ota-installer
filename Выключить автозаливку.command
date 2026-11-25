#!/bin/bash
launchctl bootout "gui/$(id -u)/com.leva.otainstaller" 2>/dev/null
rm -f "$HOME/Library/LaunchAgents/com.leva.otainstaller.plist"
echo "✔ Автозаливка выключена."
read -n 1 -s -r -p "Нажми любую клавишу, чтобы закрыть"
