#!/usr/bin/env bash
# Запустити ОДИН РАЗ у macOS-термінале з теки проєкту:
#   bash cleanup-after-transfer.sh
#
# Мостик до вашого Mac не має права видаляти файли, тому після перенесення
# залишились: службова тека _to_delete, порожні .lock-файли git і tmp_obj-сміття.
# Lock-файли блокують наступні команди git — їх треба прибрати.
set -e
cd "$(dirname "$0")"
echo "Прибираю git lock-файли…"
rm -f .git/HEAD.lock .git/index.lock .git/objects/maintenance.lock
echo "Прибираю tmp_obj-сміття ($(find .git/objects -name 'tmp_obj_*' 2>/dev/null | wc -l | tr -d ' ') файлів)…"
find .git/objects -name 'tmp_obj_*' -delete 2>/dev/null || true
echo "Прибираю _to_delete (розпакований архів і його копія)…"
rm -rf _to_delete
echo "Перевірка:"
git status --short && git log --oneline | head -3
echo
echo "Готово. Можна видалити і цей скрипт: rm cleanup-after-transfer.sh"
