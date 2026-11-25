#!/bin/bash
# Разовая настройка: создаёт репо на GitHub, включает GitHub Pages.
set -e
cd "$(dirname "$0")"
USER_=$(python3 -c 'import json;print(json.load(open("config.json"))["github_user"])')
REPO=$(python3 -c 'import json;print(json.load(open("config.json"))["repo"])')

command -v gh >/dev/null || { echo "Нет gh. Ставлю через brew..."; brew install gh; }
gh auth status >/dev/null 2>&1 || gh auth login

if [ ! -d .git ]; then
  git init -q -b main
  git add .
  git commit -q -m "AppCore: сервер"
fi

if ! gh repo view "$USER_/$REPO" >/dev/null 2>&1; then
  echo "Создаю репо $USER_/$REPO (публичное — иначе бесплатный Pages не работает)"
  gh repo create "$USER_/$REPO" --public --source . --push
else
  git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$USER_/$REPO.git"
  git push -u origin main
fi

echo "Включаю GitHub Pages (main /docs)..."
gh api -X POST "repos/$USER_/$REPO/pages" -f "source[branch]=main" -f "source[path]=/docs" >/dev/null 2>&1 \
  || gh api -X PUT "repos/$USER_/$REPO/pages" -f "source[branch]=main" -f "source[path]=/docs" >/dev/null 2>&1 || true

echo
echo "✔ Готово. Сайт: https://$USER_.github.io/$REPO/  (первый раз поднимается 1–2 мин)"
echo "Теперь кидай подписанные IPA в ipa_inbox/ и жми «Залить IPA.command»"
