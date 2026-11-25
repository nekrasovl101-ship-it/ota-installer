#!/bin/bash
# Кинь в certs_inbox папку или .zip с сертификатом (.p12 + .mobileprovision + пароль в .txt) и запусти.
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
python3 tools/ota.py cert add
echo
python3 tools/ota.py cert list
echo
read -n 1 -s -r -p "Нажми любую клавишу, чтобы закрыть"
