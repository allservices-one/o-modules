#!/usr/bin/env bash
# Чек-лист після перезавантаження (ops/inbox/0024 B). Збирає ДОКАЗИ, а не
# «все піднялося»: вивід кожної команди йде у файл, який потім цитується в
# outbox без переказу.
#
# Запускається двома шляхами:
#   · автоматично один раз після ребуту — systemd/modidx-postreboot.service;
#   · руками: bash bin/postreboot.sh
#
# Навіщо автоматично: якщо якийсь юніт не піднявся сам, це видно ЗАРАЗ, а не
# після того, як хтось прийде подивитися. 24 вересня перезавантаження може
# статися не за нашим планом, і тоді цей файл — єдиний свідок.
set -u
ROOT="${ROOT:-/srv/modidx}"
OUT="$ROOT/var/logs/postreboot-$(date -u +%Y%m%dT%H%M%SZ).txt"
mkdir -p "$ROOT/var/logs"

say(){ printf '\n===== %s\n' "$*"; }
psql(){ docker exec modidx-pg psql -U odoo -d modidx -c "$1" 2>&1; }

{
  echo "чек-лист після ребуту · $(date -u +%FT%TZ) · uptime:$(uptime -p)"
  echo "ядро: $(uname -r)"

  say "1. docker ps (мусять бути modidx-pg і modidx-caddy, healthy)"
  docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'

  say "2. шаблонні БД (усі datistemplate = t)"
  psql "SELECT datname, datistemplate FROM pg_database WHERE datname LIKE 'tmpl_%' ORDER BY 1;"

  say "3. юніти modidx-*"
  systemctl list-units --all --no-pager --no-legend 'modidx-*' | sed 's/^ *//'
  echo "-- enabled/disabled:"
  systemctl list-unit-files --no-pager --no-legend 'modidx-*'

  say "4. swap (4 ГБ)"
  swapon --show; free -m | sed -n '1p;3p'

  say "5. сайт"
  curl -sI https://allservices.one | head -4

  say "6. status.json"
  curl -s https://allservices.one/status.json | python3 -c \
    'import json,sys;d=json.load(sys.stdin);print("generated_at",d["generated_at"],"commit",d["commit"]);print("queue",d["queue"])' 2>&1

  say "7. черга: у running бути не має нічого"
  psql "SELECT state, count(*) FROM jobs GROUP BY 1 ORDER BY 1;"
  psql "SELECT id, module_id, state, locked_by, locked_at FROM jobs WHERE state='running' ORDER BY id LIMIT 20;"

  say "8. диск і пам'ять"
  df -h / | tail -1; free -m | sed -n 2p
  echo "-- образи:"; docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | sort
  echo "-- чекаути:"; du -sh "$ROOT/var/repos"/* 2>/dev/null

  say "додатково: залишкові одноразові БД прогонів (job_*) — мусить бути 0"
  psql "SELECT count(*) FROM pg_database WHERE datname LIKE 'job\_%';"
} > "$OUT" 2>&1

echo "$OUT"
cat "$OUT"
