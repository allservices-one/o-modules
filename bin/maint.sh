#!/usr/bin/env bash
# Нічне обслуговування: прибирання за прогонами, бекап, звіт відвідувань, healthcheck.
set -uo pipefail
ROOT="${ROOT:-/srv/modidx}"
cd "$ROOT"; set -a; . ./.env; set +a
# `-e ІМʼЯ` без значення: docker бере змінну з оточення. Інакше пароль лежав би
# у argv кожного виклику й світився в ps, systemctl status і journald.
export PGPASSWORD
PSQL="docker exec -i -e PGPASSWORD modidx-pg psql -U odoo -d postgres -tA"

echo "=== $(date -Is) ==="

# 1. Осиротілі БД прогонів (якщо воркер упав, не встигнувши прибрати).
#
# УВАГА: тут був баг, здатний знищити цілий нічний прохід. Умовою було просто
# datname LIKE 'job_%', а DROP ішов з WITH (FORCE) — тобто скрипт о 04:40
# обривав з'єднання ЖИВИХ прогонів і вбивав усе, що на той момент ставилося.
# Прохід на 3,5 тисячі модулів триває 10 годин і 04:40 припадає рівно на його
# середину, тому це спрацювало б у першу ж ніч.
#
# Два незалежні критерії, обидва обов'язкові:
#   1) до бази немає жодного з'єднання — живий прогін тримає його весь час;
#   2) база СТВОРЕНА понад 30 хвилин тому — закриває вузьке вікно між
#      CREATE DATABASE і першим підключенням контейнера (RUN_TIMEOUT=420 с,
#      тобто 30 хвилин із великим запасом).
#
# Вік беремо по base/<oid>/PG_VERSION, а не по теці бази, і це не дрібниця.
# Mtime ТЕКИ оновлює будь-яка робота Postgres — чекпойнт, автовакуум, — тому
# осиротіла база виглядає щойно створеною, поки сервер узагалі щось робить.
# 21.08.2026 знайдено 10 осиротілих БД на 270 МБ, створених 19.08: у всіх mtime
# теки був «сьогодні о 03:xx» (нічний прохід 17.0), тобто прибирання не
# спрацювало НІ РАЗУ за дві доби, хоча критерій виглядав правильним.
# PG_VERSION записується один раз при CREATE DATABASE і більше не торкається —
# це справжня дата створення.
$PSQL -c "
SELECT d.datname FROM pg_database d
WHERE d.datname LIKE 'job\_%'
  AND NOT EXISTS (SELECT 1 FROM pg_stat_activity a WHERE a.datname = d.datname)
  AND (pg_stat_file('base/'||d.oid||'/PG_VERSION')).modification
        < now() - interval '30 minutes'
" | while read -r db; do
  # `</dev/null` обов'язковий, і це другий бік того самого дефекту.
  # $PSQL — це `docker exec -i`, тобто він читає СВІЙ stdin, а stdin усередині
  # `while read` — це той самий список баз. Без перенаправлення перший же DROP
  # з'їдає решту рядків, і за прохід прибирається РІВНО ОДНА база.
  # Разом із хибним критерієм віку це й дало 10 осиротілих БД на 270 МБ.
  [ -n "$db" ] && $PSQL -c "DROP DATABASE IF EXISTS $db WITH (FORCE)" </dev/null >/dev/null \
    && echo "прибрано осиротілу БД $db"
done

# 2. Історія прогонів: тримаємо 5 останніх на модуль
docker exec -i -e PGPASSWORD modidx-pg psql -U odoo -d modidx -c "
WITH ranked AS (
  SELECT id, row_number() OVER (PARTITION BY module_id ORDER BY created_at DESC) rn FROM runs
) DELETE FROM runs WHERE id IN (SELECT id FROM ranked WHERE rn > 5);" >/dev/null

# 3. VACUUM
docker exec -i -e PGPASSWORD modidx-pg psql -U odoo -d modidx -c "VACUUM ANALYZE;" >/dev/null

# 4. Бекап схеми і результатів (не одноразових БД)
mkdir -p var/backups
docker exec -e PGPASSWORD modidx-pg pg_dump -U odoo -d modidx \
  | gzip > "var/backups/modidx-$(date +%F).sql.gz"
ls -1t var/backups/*.sql.gz | tail -n +8 | xargs -r rm --

# 5. Датасет і offsite-копія: коміт CSV у `data/` ЦЬОГО Ж репозиторію.
#
# Чому сюди, а не в окремий репозиторій (як планував STEPS.md 23): окремий
# вимагає нового deploy key, тобто дій власника і затримки, а offsite-копії не
# існує ВЗАГАЛІ — 21.08.2026 виявилось, що maint.timer не був увімкнений, тому
# ні pg_dump, ні пуш не робились ні разу з початку проєкту. Розділити історію
# під «чистий» репозиторій для цитування можна після 24.09: це подача, а не бекап.
# Старий код тут пушив у var/dataset/, якого на диску ніколи не було, — умова
# `if [ -d var/dataset/.git ]` робила крок тихим no-op навіть при живому таймері.
#
# series_snapshots вигружається ЦІЛКОМ, а не поточним зрізом. Це єдина
# невідтворювана частина активу: код перепише будь-хто, прогони детерміновані й
# повторюються, а рядок «станом на 19.08.2026 на 19.0 було 1 192 модулі» заднім
# числом не добудовується ні з чого.
mkdir -p data
# Пишемо через .new: якщо psql упаде, `> data/…csv` встиг би обнулити файл, і
# бекап перетворився б на видалення. Порожній вивід теж не приймаємо.
if docker exec -i -e PGPASSWORD modidx-pg psql -U odoo -d modidx -v ON_ERROR_STOP=1 -q -c \
     "COPY (SELECT taken_at, series, repos, modules, installs_ok, method
              FROM series_snapshots ORDER BY taken_at, series)
        TO STDOUT WITH (FORMAT csv, HEADER)" > var/series_snapshots.csv.new \
   && [ -s var/series_snapshots.csv.new ]; then
  mv var/series_snapshots.csv.new data/series_snapshots.csv
  echo "series_snapshots: $(( $(wc -l < data/series_snapshots.csv) - 1 )) рядків"
else
  rm -f var/series_snapshots.csv.new
  echo "УВАГА: не вдалося вигрузити series_snapshots — датасет НЕ оновлено"
fi
[ -s var/site/data/modules.csv ] && cp var/site/data/modules.csv data/modules.csv

# Комітимо ТІЛЬКИ data/ і саме через pathspec: у робочому дереві сервера цілком
# може лежати незакінчена правка сесії, і нічне обслуговування не має права
# затягнути її в публічний репозиторій. Секретів у цих CSV немає (лише
# repo/module/статуси), тому ops-check.sh тут не потрібен.
if [ -n "$(git status --porcelain -- data)" ]; then
  # `git add` перед комітом з pathspec обовʼязковий: `commit -- data` бере вміст
  # робочого дерева лише для того, що git уже відслідковує, і НОВИЙ файл
  # (перший series_snapshots.csv) без add просто не потрапив би в коміт.
  git add -- data
  if git -c user.name="modidx" -c user.email="noreply@localhost" \
       commit -qm "dataset $(date -I): series_snapshots + modules.csv" -- data; then
    if git push -q origin HEAD 2>&1; then
      echo "датасет запушено: $(git rev-parse --short HEAD)"
    else
      # Найімовірніше remote попереду (власник або сесія запушили раніше).
      # rebase робимо тільки якщо решта дерева чиста — інакше зіпсуємо роботу сесії.
      if [ -z "$(git status --porcelain)" ] && git pull -q --rebase origin main && git push -q origin HEAD; then
        echo "датасет запушено після rebase: $(git rev-parse --short HEAD)"
      else
        echo "УВАГА: коміт датасету є, push НЕ пройшов — запушити руками"
      fi
    fi
  else
    echo "УВАГА: коміт датасету не зробився"
  fi
else
  echo "датасет без змін"
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
docker exec -i -e PGPASSWORD modidx-pg psql -U odoo -d modidx -c "
UPDATE jobs SET state='queued', locked_by=NULL
WHERE state='running' AND locked_at < now() - interval '1 hour' AND attempts < 3;" >/dev/null

echo "готово"
