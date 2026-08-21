#!/usr/bin/env bash
# Похідний образ серії: одна команда від базового образу до рядка в series_image.
#
# Досі це робилося вручну: `docker build` з двома --build-arg, потім окремо
# smoke.py, потім INSERT у series_image, потім unbuildable_deps теж руками. Три
# серії пройшли — і кожна трохи інакше. 24 вересня цю послідовність доведеться
# виконати під тиском, за годину після появи гілки, і саме тоді «трохи інакше»
# коштує дорого. Тому скрипт, як уже зроблено для constraints (mkconstraints.sh).
#
#   bash bin/mkdeps.sh 16.0
#   bash bin/mkdeps.sh master modidx/odoo:master-base-20260821
#
# Порядок кроків не довільний:
#   1. constraints — з БАЗОВОГО образу, інакше pip підніме платформу й зламає base;
#   2. requirements-declared — з манифестів у БД, тому manifests.py мусить
#      відпрацювати на цій серії ДО збірки, інакше образ не покриває те, під що
#      будується (21.08.2026: 16.0 додала 35 назв, яких не було в інших серіях);
#   3. збірка;
#   4. smoke.py — і лише після нього запис у series_image. Образ, що не пройшов
#      перевірку, у прогони не потрапляє: одна зламана платформа отруює весь
#      датасет, бо КОЖЕН модуль отримує fail з нашої вини.
set -euo pipefail
SERIES="${1:?серія, напр. 16.0 або master}"
ROOT="${ROOT:-/srv/modidx}"
cd "$ROOT"
BASE="${2:-odoo:$SERIES}"
DATE="$(date -u +%Y%m%d)"
TAG="modidx/odoo:${SERIES}-deps-${DATE}"
CONS="constraints-base-${SERIES}.txt"
LOG="var/logs/build-${SERIES}-${DATE}.log"
set -a; . ./.env; set +a; export PGPASSWORD
PSQL="docker exec -i -e PGPASSWORD modidx-pg psql -U odoo -d modidx -v ON_ERROR_STOP=1"

mkdir -p var/logs
echo "== mkdeps $SERIES: база $BASE → $TAG"

# 1. Обмеження платформи
if [ -f "docker/deps/$CONS" ]; then
  echo "-- constraints: docker/deps/$CONS уже є ($(grep -vc '^#' "docker/deps/$CONS") пакетів)"
else
  bash bin/mkconstraints.sh "$BASE" "$SERIES"
fi

# 2. Список оголошених залежностей — з манифестів, не руками
python3 indexer/declared.py

# 3. Збірка. --progress=plain, бо лог читають люди й він іде у файл.
echo "-- збірка, лог: $LOG"
docker build --progress=plain \
  --build-arg "BASE=$BASE" --build-arg "CONSTRAINTS=$CONS" \
  -t "$TAG" docker/deps > "$LOG" 2>&1
grep -E '^#[0-9]+ [0-9.]+ поставлено:' "$LOG" | tail -1 || true

# 4. Що всередині: беремо з самого образу, а не з логу збірки
OK=$(docker run --rm --network none --entrypoint sh "$TAG" -c \
       'wc -l < /modidx/installed.txt' | tr -dc '0-9')
BAD=$(docker run --rm --network none --entrypoint sh "$TAG" -c \
       'wc -l < /modidx/failed.txt' | tr -dc '0-9')
echo "-- в образі: поставлено ${OK:-?}, не вдалося ${BAD:-?}"

# 5. Сторож. Без нього далі не йдемо.
python3 indexer/smoke.py "$TAG" "$SERIES"

# 6. Пакети, які не зібралися. Але «pip не поставив» ≠ «в образі немає»:
#    у `external_dependencies.python` OCA пише ІМПОРТНІ назви, а вони часто не
#    збігаються з іменами на PyPI — `OpenSSL` це pyOpenSSL, `dateutil` це
#    python-dateutil, `stdnum` це python-stdnum. Усі три вже стоять в образі
#    Odoo, тому pip чесно каже «не існує такого пакета», а модуль при цьому
#    працює. 21.08.2026 таких серед 18 невдач 16.0 було три.
#    Записати їх в unbuildable_deps означало б надрукувати неправду про чужий
#    модуль — рівно те, чого проєкт не робить. Тому кожну невдачу перевіряємо
#    імпортом у самому образі.
FAILED_RAW="var/logs/failed-${SERIES}-${DATE}.tsv"
docker run --rm --network none --entrypoint sh "$TAG" -c '
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    spec=${line%%|*}
    mod=$(printf %s "$spec" | sed "s/[<>=!~;[].*//" | tr -d " " | tr "-" "_")
    top=${mod%%.*}
    if python3 -c "import $mod" 2>/dev/null || python3 -c "import $top" 2>/dev/null; then
      printf "SAT\t%s\n" "$line"
    else
      printf "BAD\t%s\n" "$line"
    fi
  done < /modidx/failed.txt' > "$FAILED_RAW"

SAT=$(grep -c '^SAT' "$FAILED_RAW" || true)
if [ "${SAT:-0}" -gt 0 ]; then
  echo "-- не поставилось pip, але імпортується (оголошено імпортною назвою, не PyPI):"
  awk -F'\t' '$1=="SAT" {split($2,a,"|"); print "     " a[1]}' "$FAILED_RAW"
fi

#    Через COPY, а не INSERT із підстановкою: текст помилки містить лапки й
#    апострофи, і склеювати з нього SQL — прямий шлях до зламаного запиту.
$PSQL -c "DELETE FROM unbuildable_deps WHERE image_tag='$TAG'" >/dev/null
awk -F'\t' -v tag="$TAG" '$1=="BAD" {
       n=index($2,"|");
       name = n ? substr($2,1,n-1) : $2;
       err  = n ? substr($2,n+1)   : "";
       gsub(/\t/," ",err);
       print tag "\t" name "\t" err }' "$FAILED_RAW" \
  | $PSQL -c "COPY unbuildable_deps (image_tag, name, error) FROM STDIN" >/dev/null
echo "-- unbuildable_deps: $($PSQL -tAc "SELECT count(*) FROM unbuildable_deps WHERE image_tag='$TAG'") рядків (з ${BAD:-?} невдач pip)"

# 7. Тільки тепер образ стає бойовим для серії.
$PSQL -c "INSERT INTO series_image (series, image, note)
          VALUES ('$SERIES', '$TAG',
                  '$BASE + ${OK:-?} оголошених пакетів; платформа закріплена $CONS; smoke.py ✓')
          ON CONFLICT (series) DO UPDATE
            SET image = EXCLUDED.image, note = EXCLUDED.note, set_at = now()" >/dev/null
$PSQL -c "SELECT series, image, set_at FROM series_image ORDER BY series"

# 8. Знімок оточення образу (пакети, бінарники, склад ядра)
python3 indexer/inventory.py "$TAG" "$SERIES"
echo "== mkdeps $SERIES: готово, $TAG"
