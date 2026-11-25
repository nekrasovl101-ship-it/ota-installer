#!/bin/bash
cd "$(dirname "$0")"
python3 tools/ota.py add
echo
read -n 1 -s -r -p "Нажми любую клавишу, чтобы закрыть"
