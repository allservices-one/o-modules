#!/usr/bin/env bash
# Нічне обслуговування: прибирання за прогонами, бекап, звіт відвідувань, healthcheck.
set -uo pipefail
ROOT="${ROOT:-/srv/modidx}"
cd "$ROOT"; set -a; . ./.env; set +a
PSQL="docker exec -i -e PGPASSWORD=$PGPASSWORD modidx-pg psql -U odoo -d postgres -tA"

echo "=== $(date -Is) ==="

# 1. Осиротілі БД прогонів (якщо воркер упав, не встигнувши прибрати)
$PSQL -c "SELECT datname FROM pg_database WHERE datname LIKE 'job\_%'" | while read -r db; do
  [ -n "$db" ] && $PSQL -c "DROP DATABASE IF EXISTS $db WITH (FORCE)" >/dev/null && echo "прибрано БД $db"
done

# 2. Історія прогонів: тримаємо 5 останніх на модуль
docker exec -i -e PGPASSWORD="$PGPASSWORD" modidx-pg psql -U odoo -d modidx -c "
WITH ranked AS (
  SELECT id, row_number() OVER (PARTITION BY module_id ORDER BY created_at DESC) rn FROM runs
) DELETE FROM runs WHERE id IN (SELECT id FROM ranked WHERE rn > 5);" >/dev/null

# 3. VACUUM
docker exec -i -e PGPASSWORD="$PGPASSWORD" modidx-pg psql -U odoo -d modidx -c "VACUUM ANALYZE;" >/dev/null

# 4. Бекап схеми і результатів (не одноразових БД)
mkdir -p var/backups
docker exec -e PGPASSWORD="$PGPASSWORD" modidx-pg pg_dump -U odoo -d modidx \
  | gzip > "var/backups/modidx-$(date +%F).sql.gz"
ls -1t var/backups/*.sql.gz | tail -n +8 | xargs -r rm --

# 5. Offsite і водночас публічний датасет: пуш CSV у git-репозиторій (безкоштовно)
if [ -d var/dataset/.git ]; then
  cp var/site/data/modules.csv var/dataset/ 2>/dev/null || true
  cp var/oca_modules.csv var/dataset/ 2>/dev/null || true
  (cd var/dataset && git add -A && \
   git -c user.name="modidx" -c user.email="noreply@localhost" \
     commit -qm "dataset $(date -I)" 2>/dev/null && git push -q origin HEAD 2>/dev/null) \
   && echo "датасет запушено"
fi

# 6. Звіт відвідувань зі логів Caddy (нуль постійної памʼяті)
if command -v goaccess >/dev/null && [ -f var/caddy/logs/access.log ]; then
  goaccess var/caddy/logs/access.log --log-format=CADDY -o var/site/stats.html \
    --no-progress --ignore-crawlers 2>/dev/null && echo "stats.html оновлено"
fi

# 7. Прибирання образів і кешу Docker
docker image prune -f >/dev/null
docker builder prune -f >/dev/null 2>&1

# 8. Healthcheck: диск, память, черга, свіжість прогонів
DISK=$(df --output=pcent / | tail -1 | tr -dc '0-9')
QUEUE=$($PSQL -d modidx -c "SELECT count(*) FROM jobs WHERE state='queued'" 2>/dev/null | tr -dc '0-9')
STUCK=$($PSQL -d modidx -c "SELECT count(*) FROM jobs WHERE state='running' AND locked_at < now() - interval '1 hour'" 2>/dev/null | tr -dc '0-9')
FRESH=$($PSQL -d modidx -c "SELECT count(*) FROM runs WHERE created_at > now() - interval '24 hours'" 2>/dev/null | tr -dc '0-9')
echo "диск ${DISK}% · у черзі ${QUEUE:-?} · зависло ${STUCK:-0} · прогонів за добу ${FRESH:-0}"

PROBLEM=""
[ "${DISK:-0}" -gt 85 ] && PROBLEM="${PROBLEM}диск заповнений на ${DISK}%. "
[ "${STUCK:-0}" -gt 5 ] && PROBLEM="${PROBLEM}зависло задач: ${STUCK}. "
[ "${FRESH:-0}" -lt 10 ] && [ "${QUEUE:-0}" -gt 100 ] && PROBLEM="${PROBLEM}воркери не працюють: за добу лише ${FRESH} прогонів при ${QUEUE} у черзі. "
if [ -n "$PROBLEM" ]; then
  python3 bin/notify.py "modidx: потрібна увага" "$PROBLEM" || echo "не вдалося надіслати лист"
fi

# 9. Завислі задачі — повернути в чергу
docker exec -i -e PGPASSWORD="$PGPASSWORD" modidx-pg psql -U odoo -d modidx -c "
UPDATE jobs SET state='queued', locked_by=NULL
WHERE state='running' AND locked_at < now() - interval '1 hour' AND attempts < 3;" >/dev/null

echo "готово"
