#!/usr/bin/env bash
# Шаблонна БД з установленим base для однієї серії Odoo.
# Далі кожен прогін робить CREATE DATABASE ... TEMPLATE — 1-2 с замість 30-60 с.
set -euo pipefail
S="${1:?вкажіть серію, напр. 19.0}"
ROOT="${ROOT:-/srv/modidx}"
cd "$ROOT"; set -a; . ./.env; set +a
TMPL="tmpl_$(echo "$S" | tr -d '.')"

psql(){ docker exec -i -e PGPASSWORD="$PGPASSWORD" modidx-pg psql -U odoo -d postgres -v ON_ERROR_STOP=1 "$@"; }

if psql -tAc "SELECT 1 FROM pg_database WHERE datname='$TMPL'" | grep -q 1; then
  echo "  $TMPL уже існує"; exit 0
fi

echo "  створюю $TMPL (установка base на Odoo $S)…"
psql -c "CREATE DATABASE ${TMPL}_build"
docker run --rm --network modidx --memory=2g \
  -e PGPASSWORD="$PGPASSWORD" \
  "odoo:$S" odoo \
    -d "${TMPL}_build" --db_host=pg --db_user=odoo --db_password="$PGPASSWORD" \
    -i base --without-demo=all --stop-after-init --no-http \
    --max-cron-threads=0 --log-level=warn

# позначаємо як шаблон: read-only, з нього швидко клонуються робочі БД
psql -c "ALTER DATABASE ${TMPL}_build RENAME TO $TMPL"
psql -c "UPDATE pg_database SET datistemplate=true, datallowconn=true WHERE datname='$TMPL'"
echo "  готово: $TMPL"
