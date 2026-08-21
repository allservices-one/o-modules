#!/usr/bin/env bash
# Образ серії `master` із датованого нічного .deb. Ідемпотентний за датою.
#
#   bash bin/mkmaster.sh              # .deb за сьогодні
#   bash bin/mkmaster.sh 20260821     # конкретна дата
#
# Далі — вручну, бо кожен крок має право сказати «ні»:
#   python3 indexer/smoke.py modidx/odoo:master-<дата> master
#   bash bin/mkconstraints.sh modidx/odoo:master-<дата> master
#   docker build --build-arg BASE=modidx/odoo:master-<дата> \
#     --build-arg CONSTRAINTS=constraints-base-master.txt \
#     -t modidx/odoo:master-deps-<дата> docker/deps
#   python3 indexer/inventory.py <образ> master
#   psql: INSERT INTO series_image (series, image, note) …
set -euo pipefail
ROOT="${ROOT:-/srv/modidx}"
D="${1:-$(date -u +%Y%m%d)}"
BASE="${BASE:-odoo:19.0}"
CTX="$ROOT/var/build/master"
TAG="modidx/odoo:master-$D"

# Індекс каталогу читаємо, а не вгадуємо ім'я файла: версія майстра змінюється
# (зараз 19.5a1, після 24.09 буде інша), а дата в імені — ні.
IDX="https://nightly.odoo.com/master/nightly/deb/"
DEB=$(curl -sS --max-time 60 "$IDX" | grep -oE "odoo_[0-9a-zA-Z.+~-]*\.${D}_all\.deb" | sort -u | tail -1)
[ -n "$DEB" ] || { echo "mkmaster: .deb за $D у $IDX не знайдено" >&2; exit 1; }

mkdir -p "$CTX"
if [ ! -s "$CTX/$DEB" ]; then
  echo "== завантажую $DEB"
  curl -sS --max-time 900 -o "$CTX/$DEB.part" "$IDX$DEB"
  mv "$CTX/$DEB.part" "$CTX/$DEB"
fi
ls -lh "$CTX/$DEB" | awk '{print "   " $9 " — " $5}'
cp "$ROOT/docker/master/Dockerfile" "$CTX/Dockerfile"

echo "== збираю $TAG з BASE=$BASE"
time docker build --build-arg "BASE=$BASE" --build-arg "DEB=$DEB" \
  --build-arg "DEB_URL=$IDX$DEB" -t "$TAG" "$CTX"

# .deb у контексті — 100+ МБ на серію. Диск тут дорожчий за повторне
# завантаження: воно триває хвилину, а місце потрібне під п'яту серію.
rm -f "$CTX/$DEB"
echo "== готово: $TAG (з $DEB)"
docker image inspect "$TAG" --format '   розмір: {{.Size}} байт · odoo: {{index .Config.Labels "modidx.deb"}}'
