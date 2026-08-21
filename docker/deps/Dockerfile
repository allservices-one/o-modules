# Похідний образ: офіційний Odoo плюс РІВНО ті python-пакети, які модулі
# оголосили в external_dependencies своїх манифестів.
#
# Навіщо: 112 прогонів зі 1164 (8,2%) впали в `env` — «перевірити не вдалося з
# нашої вини». На сторінці, що обіцяє факти, «не знаємо» шкодить сильніше за
# «зламано».
#
# Чого тут НЕ роблять: не додають пакетів на власний розсуд. Список
# генерується з манифестів (requirements-declared.txt), інакше ми перевіряємо
# власне середовище замість того, яке декларує вендор, і результат перестає
# щось означати.
#
# Кожен пакет ставиться ОКРЕМО: один непридатний (немає wheel, потрібен
# компілятор, конфлікт) не має валити збірку решти. Невдачі лишаються у
# /modidx/failed.txt і потім потрапляють у таблицю unbuildable_deps — це не
# наша проблема середовища, а факт про модуль, який таке оголосив.
# Платформа закріплена constraints-файлом, і це не перестраховка.
# Без нього транзитивна залежність підняла cryptography до 50.0.0, той зламав
# системний pyOpenSSL 23.2.0, і в образі перестав вантажитись сам base Odoo —
# тобто КОЖЕН модуль отримав би fail з нашої вини. Ми додаємо пакети, а не
# переписуємо платформу. Пакет, який вимагає новішу версію платформного —
# не ставиться, і це чесний результат про модуль, що його оголосив.
# Таймаут на ПАКЕТ, не на збірку. 21.08.2026 збірка 16.0 стояла 21 хвилину на
# `ortools`: constraints тримають protobuf і numpy на версіях образу, придатної
# збірки ortools під них немає, і резолвер pip перебирав версію за версією на
# 80% CPU — без межі це нескінченно. Пакет, який не ставиться за 5 хвилин, —
# це факт про модуль, що його оголосив (unbuildable_deps), а не причина
# зупинити збірку решти 134.
ARG PIP_TIMEOUT=300
ARG BASE=odoo:19.0
ARG CONSTRAINTS=constraints-base-19.0.txt
FROM ${BASE}
USER root
ARG CONSTRAINTS
ARG PIP_TIMEOUT
COPY requirements-declared.txt /modidx/requirements-declared.txt
COPY ${CONSTRAINTS} /modidx/constraints.txt
# --break-system-packages з'явився в pip 23.0. В odoo:17.0 стоїть pip 22.0.2,
# і з жорстко прописаним прапорцем там ПАДАЮТЬ ВСІ 133 встановлення — образ
# виходить порожній, але зовні справний. Тому прапорець визначається на місці.
RUN set -eu; \
    mkdir -p /modidx; : > /modidx/failed.txt; : > /modidx/installed.txt; \
    PIPF=""; \
    pip install --help 2>/dev/null | grep -q -- --break-system-packages \
      && PIPF="--break-system-packages"; \
    echo "pip: $(pip --version) · прапорці: ${PIPF:-немає}"; \
    while IFS= read -r p; do \
      case "$p" in ''|'#'*) continue;; esac; \
      if timeout ${PIP_TIMEOUT} pip install --no-cache-dir $PIPF \
           -c /modidx/constraints.txt "$p" >/tmp/pip.log 2>&1; then \
        echo "$p" >> /modidx/installed.txt; \
      elif [ $? -eq 124 ]; then \
        echo "$p|ТАЙМАУТ ${PIP_TIMEOUT} с: резолвер pip не збігся" >> /modidx/failed.txt; \
      else \
        echo "$p|$(tail -c 300 /tmp/pip.log | tr '\n' ' ')" >> /modidx/failed.txt; \
      fi; \
    done < /modidx/requirements-declared.txt; \
    echo "поставлено: $(wc -l < /modidx/installed.txt), не вдалося: $(wc -l < /modidx/failed.txt)"
USER odoo
