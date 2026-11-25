#!/bin/bash
# Выкладывает изменения: каталог (GitHub Pages) + IPA Signer (Cloudflare).
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
echo "== Каталог (GitHub)"
mkdir -p .github/workflows && cp tools/workflows/*.yml .github/workflows/
git add -A && git commit -q -m "Обновление сайтов" || echo "(в каталоге без изменений)"
git pull -q --rebase --autostash && git push -q && echo "✔ каталог выложен (обновится через ~1 мин)"
if [ -f worker/wrangler.toml ]; then
  echo "== IPA Signer (Cloudflare)"
  (cd worker && npx -y wrangler@latest deploy 2>&1 | grep -E "https://|Uploaded|Deployed|rror") && echo "✔ IPA Signer выложен"
fi
echo
read -n 1 -s -r -p "Нажми любую клавишу, чтобы закрыть"
