#!/usr/bin/env bash
# Зріз розподілу по серіях для публікації (ops/inbox/2026-08-21T1600b A).
#
# Навіщо окремий скрипт, а не запит руками: цифри в публікації мусять бути
# відтворюваними через півроку, тому знімати їх треба ОДНИМ І ТИМ САМИМ способом,
# а не тим, що згадалося. Вивід кладеться в outbox дослівно.
#
#   bash bin/series-snapshot.sh            # усі серії
#   bash bin/series-snapshot.sh 16.0       # одна
set -uo pipefail
ROOT="${ROOT:-/srv/modidx}"
cd "$ROOT"; set -a; . ./.env; set +a; export PGPASSWORD
PSQL="docker exec -i -e PGPASSWORD modidx-pg psql -U odoo -d modidx"
S="${1:-}"
W=""; [ -n "$S" ] && W="WHERE series = '$S'"

echo "=== зріз $(date -u +%FT%TZ) · commit $(git rev-parse --short HEAD) ==="
echo
echo "--- 1. latest_runs: серія × статус (дослівно, як просив 1600b A) ---"
$PSQL -c "SELECT series, status, count(*) FROM latest_runs $W GROUP BY 1,2 ORDER BY 1,2;"

echo "--- 2. знаменники по серіях ---"
# runnable = те, що ми маємо право проганяти: availability=open_source і
# installable не false. Саме воно й є знаменником публічного відсотка.
$PSQL -c "
SELECT m.series,
       count(*)                                                        AS total,
       count(*) FILTER (WHERE m.installable IS FALSE)                  AS not_installable,
       count(*) FILTER (WHERE m.availability='open_source'
                          AND m.installable IS DISTINCT FROM false)    AS runnable,
       count(r.id)                                                     AS verified,
       count(*) FILTER (WHERE m.availability='open_source'
                          AND m.installable IS DISTINCT FROM false
                          AND r.id IS NULL)                            AS pending
FROM modules m LEFT JOIN latest_runs r ON r.module_id = m.id
${W/series/m.series}
GROUP BY 1 ORDER BY 1;"

echo "--- 3. черга (0/0 = прохід завершено) ---"
$PSQL -c "SELECT state, count(*) FROM jobs GROUP BY 1 ORDER BY 1;"

echo "--- 4. образ, на якому отримані результати ---"
$PSQL -c "SELECT series, odoo_image, count(*) FROM latest_runs $W GROUP BY 1,2 ORDER BY 1,2;"

echo "--- 5. версія правил класифікатора ---"
$PSQL -c "SELECT rules_version, count(*) FROM latest_runs $W GROUP BY 1 ORDER BY 2 DESC;"
