#!/usr/bin/env bash
# Файл обмежень платформи для похідного образу: рівно `pip list` базового образу.
#
# Навіщо: pip НЕ МАЄ ПРАВА піднімати версії, які прийшли з офіційним образом.
# 19.08.2026 транзитивна залежність підняла cryptography до 50.0.0, той зламав
# системний pyOpenSSL 23.2.0, і в такому образі падав сам base Odoo — тобто
# КОЖЕН модуль отримав би fail з нашої вини (indexer/smoke.py).
#
# Раніше ці файли робилися руками для кожної серії. Руками — означає «по-різному
# для master і для 20.0 у вересні, під тиском». Тому скрипт.
#
#   bash bin/mkconstraints.sh odoo:16.0 16.0
#   bash bin/mkconstraints.sh modidx/odoo:master-base-20260821 master
set -euo pipefail
IMAGE="${1:?образ, напр. odoo:16.0}"
SERIES="${2:?ключ серії, напр. 16.0 або master}"
ROOT="${ROOT:-/srv/modidx}"
OUT="$ROOT/docker/deps/constraints-base-$SERIES.txt"

list=$(docker run --rm --network none --entrypoint sh "$IMAGE" -c \
  "pip list --format=freeze --disable-pip-version-check 2>/dev/null \
   || pip3 list --format=freeze --disable-pip-version-check")
[ -n "$list" ] || { echo "mkconstraints: pip list порожній — образ $IMAGE не той" >&2; exit 1; }

{
  echo "# Версії, які йдуть у образі $IMAGE (серія $SERIES). Згенеровано"
  echo "# bin/mkconstraints.sh $(date -u +%Y-%m-%d), не правити руками."
  echo "# Призначення: pip НЕ МАЄ ПРАВА їх піднімати. Транзитивне оновлення"
  echo "# cryptography зламало системний pyOpenSSL, і в такому образі падав"
  echo "# сам base Odoo — кожен модуль отримав би fail з нашої вини."
  printf '%s\n' "$list"
} > "$OUT"
echo "mkconstraints: $OUT — $(grep -vc '^#' "$OUT") пакетів"
