#!/bin/bash
# Один раз: поднимает сайт, где люди заливают свой IPA, выбирают сертификат и ставят.
# Сайт — Cloudflare Workers (бесплатно), файлы — приватный репо ota-uploads, подпись — GitHub Actions.
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
set -e
USER_=$(python3 -c 'import json;print(json.load(open("config.json"))["github_user"])')
REPO=$(python3 -c 'import json;print(json.load(open("config.json"))["repo"])')
MAIN="$USER_/$REPO"; UP="$USER_/ota-uploads"
SITE="https://$USER_.github.io/$REPO"
pause() { read -n 1 -s -r -p "Нажми любую клавишу, чтобы закрыть"; }
trap 'echo; echo "✖ Что-то пошло не так (строка $LINENO). Скинь скрин этого окна."; pause' ERR

echo "== 1/5 Приватный репо для файлов людей"
if ! gh repo view "$UP" >/dev/null 2>&1; then
  gh repo create "$UP" --private --add-readme -d "Временные файлы AppCore" >/dev/null
  sleep 3
fi
gh release view uploads -R "$UP" >/dev/null 2>&1 || gh release create uploads -R "$UP" -t uploads -n "временные файлы, удаляются через 2 часа" >/dev/null
echo "✔ $UP"

echo "== 2/5 Токен для сайта"
if [ ! -s .token_people ]; then
  echo "Откроется страница нового токена. Заполни:"
  echo "  • Token name: OTA Sign Site"
  echo "  • Expiration: максимальный срок"
  echo "  • Repository access: Only select repositories → ota-installer И ota-uploads (оба!)"
  echo "  • Permissions → Add permissions: Actions → Read and write, Contents → Read and write"
  echo "  • Generate token → скопируй (github_pat_...)"
  open "https://github.com/settings/personal-access-tokens/new"
  read -r -p "Вставь токен и нажми Enter: " T
  [ -z "$T" ] && { echo "Пусто"; pause; exit 1; }
  printf '%s' "$T" > .token_people && chmod 600 .token_people
fi
[ -s .callback_key ] || { openssl rand -hex 24 > .callback_key; chmod 600 .callback_key; }
gh secret set GH_PAT -R "$MAIN" < .token_people
gh secret set CALLBACK_KEY -R "$MAIN" < .callback_key
echo "✔ секреты на GitHub"

echo "== 3/5 Cloudflare (вход через браузер)"
command -v npx >/dev/null || { echo "Ставлю Node.js…"; brew install node; }
cd worker
npx -y wrangler@latest whoami 2>/dev/null | grep -qi "associated with the email" || npx -y wrangler@latest login
if [ ! -s .kv_id ]; then
  npx -y wrangler@latest kv namespace create OTA_JOBS 2>&1 | tee /tmp/ota_kv.txt
  grep -oE '[0-9a-f]{32}' /tmp/ota_kv.txt | head -1 > .kv_id
fi
KV=$(cat .kv_id); [ -z "$KV" ] && { echo "✖ не создалось хранилище KV"; pause; exit 1; }
sed -e "s#__SITE__#$SITE#" -e "s#__MAIN_REPO__#$MAIN#" -e "s#__UPLOADS_REPO__#$UP#" -e "s#__KV_ID__#$KV#" \
    wrangler.toml.template > wrangler.toml

echo "== 4/5 Выкладываю сайт"
npx -y wrangler@latest deploy 2>&1 | tee /tmp/ota_deploy.txt
URL=$(grep -oE 'https://[a-z0-9.-]+\.workers\.dev' /tmp/ota_deploy.txt | head -1)
[ -z "$URL" ] && { echo "✖ не понял адрес сайта"; pause; exit 1; }
npx -y wrangler@latest secret put GH_TOKEN < ../.token_people >/dev/null
npx -y wrangler@latest secret put CALLBACK_KEY < ../.callback_key >/dev/null
cd ..

echo "== 5/5 Обновляю сервер на GitHub"
python3 - "$URL" "$UP" <<'PY'
import json, sys
c = json.load(open("config.json")); c["worker_url"] = sys.argv[1]; c["uploads_repo"] = sys.argv[2]
json.dump(c, open("config.json", "w"), ensure_ascii=False, indent=2)
PY
mkdir -p .github/workflows && cp tools/workflows/*.yml .github/workflows/
git add -A && git commit -q -m "Подпись для людей" || true
git pull -q --rebase --autostash && git push -q
echo "$URL" > "../Сайт для людей.txt"

echo
echo "✔ Готово! Сайт для людей:"
echo "   $URL"
echo "Скидывай эту ссылку. Человек: выбрал IPA → сертификат → «Подписать» → «Установить»."
echo "(ссылка ещё лежит в файле «Сайт для людей.txt» в папке AppCore)"
open "$URL"
echo
pause
