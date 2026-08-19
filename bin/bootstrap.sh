#!/usr/bin/env bash
# Розгортання на чистому Debian/Ubuntu VPS (4 vCPU / 8 GB / 80 GB).
# Ідемпотентний: можна запускати повторно.
set -euo pipefail
ROOT="${ROOT:-/srv/modidx}"
SERIES="${SERIES:-18.0 19.0}"

log(){ printf '\n\033[1m== %s\033[0m\n' "$*"; }

log "1. Пакети"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
apt-get update -qq
apt-get install -y -qq git python3 python3-psycopg2 goaccess jq ca-certificates >/dev/null

log "2. Swap 4 GB (страховка від OOM на 8 GB RAM)"
if [ ! -f /swapfile ]; then
  fallocate -l 4G /swapfile
  chmod 600 /swapfile
  mkswap -q /swapfile
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
sysctl -qw vm.swappiness=10
grep -q 'vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' >> /etc/sysctl.conf

log "3. Теки"
mkdir -p "$ROOT"/{var/pgdata,var/site,var/logs,var/caddy/{data,config,logs},var/repos,var/pool,var/backups}
cd "$ROOT"

log "4. .env"
if [ ! -f .env ]; then
  echo "PGPASSWORD=$(head -c 24 /dev/urandom | base64 | tr -d '/+=' )" > .env
  chmod 600 .env
  echo "  створено .env з випадковим паролем Postgres"
fi

log "5. Обмеження логів Docker (щоб не з'їли 80 GB)"
mkdir -p /etc/docker
if [ ! -f /etc/docker/daemon.json ]; then
  cat > /etc/docker/daemon.json <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
JSON
  systemctl restart docker
fi

log "6. Образи Odoo"
for s in $SERIES; do
  docker image inspect "odoo:$s" >/dev/null 2>&1 || docker pull "odoo:$s"
done

log "7. Postgres (Caddy НЕ піднімаємо — чекає на AAAA, див. Caddyfile)"
docker compose up -d pg
until docker exec modidx-pg pg_isready -U odoo -d modidx >/dev/null 2>&1; do sleep 2; done

log "8. Схема"
set -a; . ./.env; set +a
docker exec -i -e PGPASSWORD="$PGPASSWORD" modidx-pg psql -U odoo -d modidx < indexer/schema.sql

log "9. Шаблонні БД"
for s in $SERIES; do bin/mktemplate.sh "$s"; done

log "10. Готово"
cat <<TXT

Далі:
  bin/sync_repos.sh          # чекаути OCA і плоский пул адонів (~6 GB)
  python3 indexer/harvest.py # зріз гілок і модулів (2 хв)
  python3 indexer/enqueue.py # поставити прогони в чергу
  python3 indexer/runner.py  # воркер (запускати 2 копії через systemd)
  python3 indexer/export.py  # згенерувати статику в var/site

Перед публікацією: вписати свій домен у Caddyfile і  docker compose up -d caddy
TXT
