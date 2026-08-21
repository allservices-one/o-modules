#!/usr/bin/env bash
# Шаблонна БД з установленим base для однієї серії Odoo.
# Далі кожен прогін робить CREATE DATABASE ... TEMPLATE — 1-2 с замість 30-60 с.
set -euo pipefail
S="${1:?вкажіть серію, напр. 19.0}"
IMG="${2:-}"          # образ; порожньо → series_image, далі odoo:$S
ROOT="${ROOT:-/srv/modidx}"
cd "$ROOT"; set -a; . ./.env; set +a
TMPL="tmpl_$(echo "$S" | tr -d '.')"

export PGPASSWORD PASSWORD="$PGPASSWORD"
# `-e ІМʼЯ` без значення: docker бере змінну з оточення, і пароль не потрапляє
# в argv, тобто не світиться в ps, systemctl status і journald.
psql(){ docker exec -i -e PGPASSWORD modidx-pg psql -U odoo -d postgres -v ON_ERROR_STOP=1 "$@"; }

if psql -tAc "SELECT 1 FROM pg_database WHERE datname='$TMPL'" | grep -q 1; then
  echo "  $TMPL уже існує"; exit 0
fi

# Образ БЕРЕТЬСЯ З series_image, а не збирається з імені серії. Літерал
# `odoo:$S` працює лише поки серія називається як тег офіційного образу: для
# `master` такого тегу не існує взагалі, а 24.09 перехід на 20.0 мусить бути
# зміною одного рядка в таблиці, а не правкою скрипта під тиском
# (ops/inbox/0022, чек-лист 1 і 3).
if [ -z "$IMG" ]; then
  IMG=$(docker exec -i -e PGPASSWORD modidx-pg psql -U odoo -d modidx -tAc \
        "SELECT image FROM series_image WHERE series='$S'" 2>/dev/null | tr -d '[:space:]' || true)
fi
[ -n "$IMG" ] || IMG="odoo:$S"
docker image inspect "$IMG" >/dev/null 2>&1 || {
  echo "  образу $IMG немає локально — спершу зберіть або docker pull" >&2; exit 1; }

echo "  створюю $TMPL (установка base, образ $IMG)…"
psql -c "DROP DATABASE IF EXISTS ${TMPL}_build"
psql -c "CREATE DATABASE ${TMPL}_build"
# Параметри БД передаються ТІЛЬКИ через env, не через флаги.
# Entrypoint офіційного образу збирає DB_ARGS з env (HOST по замовчуванню 'db',
# PASSWORD — 'odoo') і дописує їх у кінець: exec odoo "$@" "${DB_ARGS[@]}".
# Тобто будь-який наш --db_host / --db_password перебивається. Перевірено 19.08.2026:
# з флагами падало на 'could not translate host name "db"'.
docker run --rm --network modidx --memory=2g \
  -e HOST=pg -e PORT=5432 -e USER=odoo -e PASSWORD \
  "$IMG" odoo \
    -d "${TMPL}_build" \
    -i base --without-demo=all --stop-after-init --no-http \
    --max-cron-threads=0 --log-level=warn

# позначаємо як шаблон: read-only, з нього швидко клонуються робочі БД
psql -c "ALTER DATABASE ${TMPL}_build RENAME TO $TMPL"
psql -c "UPDATE pg_database SET datistemplate=true, datallowconn=true WHERE datname='$TMPL'"
echo "  готово: $TMPL"
