#!/usr/bin/env python3
"""Ставить у чергу прогони: усе, що ще не тестувалося або чий head_sha змінився.

Пріоритети: спершу те, що люди дивитимуться найчастіше.
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from db import connect, ROOT, SERIES

CANDIDATES = """
  SELECT m.id, m.series, m.repo, m.module,
         CASE
           WHEN m.series = (SELECT max(series) FROM modules) THEN 10  -- найновіша серія найважливіша
           ELSE 100
         END AS prio
  FROM modules m
  LEFT JOIN latest_runs r ON r.module_id = m.id
  WHERE m.series = %s
    -- installable=false — модуль САМ заявляє, що не встановлюється:
    -- метапакет, залишок _unported, оболонка для депрекації. Ставити
    -- його в чергу означає витратити прогін, щоб дізнатися те, що вже
    -- написано в манифесті, і отримати env, який не є інформацією.
    -- NULL тут ЗАЛИШАЄМО в черзі: це «манифест ще не розібрано»,
    -- а не «не встановлюваний» — плутати ці стани не можна.
    AND m.installable IS DISTINCT FROM false
    -- прогонабельна лише те, що ми можемо дістати
    AND m.availability = 'open_source'
    AND (r.id IS NULL OR r.head_sha IS DISTINCT FROM m.head_sha)
    AND NOT EXISTS (
      SELECT 1 FROM jobs j
      WHERE j.module_id = m.id AND j.state IN ('queued','running')
    )
"""


def in_pool(series, module):
    """Чи є тека модуля в плоскому пулі адонів.

    Без цієї перевірки черга наповнюється модулями, яких немає на диску, і це
    не теорія. `harvest.py` бачить гілки через `git ls-remote`, тобто дізнається
    про новий модуль ЗА ХВИЛИНИ до того, як `sync_repos.sh` покладе його тека в
    пул. Порядок `harvest → sync_repos → manifests → enqueue` тримає
    `modidx-harvest.service`, але будь-який ручний запуск harvest поза юнітом цей
    порядок ламає — 21.08.2026 саме так сталося: 8 нових модулів 19.0 попали в
    чергу без тек, і прогін дав 4 × `not_installed_despite_rc0` плюс 12 сусідів
    по батчу з `env_module_not_found`. Вердикт при цьому чесний (`env`, не
    `fail`), але це 16 витрачених прогонів по 1,5 хвилини й сміття в історії.

    Перевірка коштує один `stat` на модуль. Правило те саме, що й для
    `ir_module_module`: питати систему, а не покладатися на порядок кроків.
    """
    return (ROOT / "var" / "pool" / series / module).exists()


def main():
    conn = connect(); cur = conn.cursor()
    total = 0
    for s in SERIES:
        cur.execute(CANDIDATES, (s,))
        rows = cur.fetchall()
        ready = [r for r in rows if in_pool(s, r["module"])]
        missing = [r for r in rows if not in_pool(s, r["module"])]
        if ready:
            cur.executemany("INSERT INTO jobs (module_id, series, priority) VALUES (%s,%s,%s)",
                            [(r["id"], r["series"], r["prio"]) for r in ready])
        print(f"  {s}: у чергу додано {len(ready)}"
              + (f", пропущено без теки в пулі {len(missing)}" if missing else ""))
        # Пропущені друкуємо поіменно: це або незапущений sync_repos (лікується
        # запуском), або модуль, який зник із гілки між зрізом і чекаутом.
        for r in missing[:20]:
            print(f"      немає var/pool/{s}/{r['module']} (репо {r['repo']})", file=sys.stderr)
        if len(missing) > 20:
            print(f"      … і ще {len(missing)-20}", file=sys.stderr)
        total += len(ready)
    cur.execute("SELECT state, count(*) c FROM jobs GROUP BY state ORDER BY state")
    print("\nчерга:", {r["state"]: r["c"] for r in cur.fetchall()})
    conn.close()
    print(f"всього додано: {total}")


if __name__ == "__main__":
    main()
