#!/usr/bin/env bash
# Пул адонів для серії `master`: ті самі модулі 19.0, інша платформа.
#
# У OCA гілки `master` немає (перевірено: є в чотирьох репозиторіях, у всіх нуль
# тек із `__manifest__.py`). Тому «серія master» у нас означає не гілку, а
# **ціль прогону**: беремо код 19.0 БЕЗ ЗМІН і ставимо його на образ лінії
# розробки. Різниця у вердикті тоді за побудовою належить платформі, а не модулю —
# код байт-у-байт той самий, що вже дав результат на 19.0.
#
# Чому потрібен окремий скрипт, а не SERIES=master у sync_repos.sh: той клонує
# гілку з іменем серії, а тут клонувати нічого. Змішати ці дві речі в одному
# скрипті означало б, що 24.09 хтось спробує «синхронізувати master» і отримає
# порожній пул.
#
# Чому var/repos/master — симлінк, а не копія. runner монтує РІВНО
# `var/repos/<серія>` тим самим абсолютним шляхом (щоб симлінки пулу
# резолвились у контейнері). Якби пул master вказував на `var/repos/19.0/...`,
# ця тека всередині контейнера не була б змонтована, симлінки повисли б у
# нікуди, і Odoo написала б «invalid module names, ignored» при коді виходу 0 —
# тобто прогін НЕ відбувся, а виглядав як успіх. Той самий дефект, що спіймали
# 19.08 на першому пулі. Симлінк дає docker шлях `.../repos/master`, який
# резолвиться в реальні чекаути 19.0.
set -euo pipefail
ROOT="${ROOT:-/srv/modidx}"
SRC="${1:-19.0}"
cd "$ROOT"

[ -d "var/repos/$SRC" ] || { echo "немає var/repos/$SRC — спершу sync_repos.sh" >&2; exit 1; }

if [ -L var/repos/master ]; then
  echo "var/repos/master -> $(readlink var/repos/master) (уже є)"
elif [ -e var/repos/master ]; then
  echo "var/repos/master існує і НЕ симлінк — не чіпаю" >&2; exit 1
else
  ln -s "$SRC" var/repos/master
  echo "створено симлінк var/repos/master -> $SRC"
fi

rm -rf var/pool/master
mkdir -p var/pool/master
n=0
for d in var/repos/master/*/*/; do
  [ -f "$d/__manifest__.py" ] || continue
  m=$(basename "$d")
  ln -sfn "$ROOT/${d%/}" "var/pool/master/$m"
  n=$((n+1))
done
echo "пул master: $n модулів (код $SRC на платформі лінії розробки)"
