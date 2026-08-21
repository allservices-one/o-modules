#!/usr/bin/env python3
"""Рейтинг блокувальників міграції: хто тримає інших на старій серії.

`ops/inbox/2026-08-21T1600b` B. Головна відмінність від «65% не перенесено»: та
цифра — констатація, а ця таблиця називає **причину** і дає адресат дії.

## Що таке блокувальник, і чому перший підхід брехав

Наївний рейтинг «скільком модулям я потрібен» ставить на перше місце `queue_job`:
30 залежних, 18 з них без 19.0. Але `queue_job` **сам має гілку 19.0** — тобто він
нікого не тримає, його залежні просто не перенесли. Назвати його блокувальником
означало б надрукувати неправду про робочий модуль.

Блокувальник — це модуль, у якого **немає гілки наступної серії** і від якого
залежать інші. Тільки він робить міграцію залежних неможливою, а не лише
незробленою.

## Три правила з 1600b, кожне міняє результат

1. **Лише прямі залежності.** Транзитивні дали б `base` і `account` на першому
   місці — арифметично правильно, по суті безглуздо.
2. **Ядро Odoo виключене.** Беремо `core_addons` для потрібної серії, тобто
   фактичний склад образу, а не список у коді: він різний між серіями (546 на
   16.0, 686 на 18.0, 691 на master).
3. **`auto_install` окремо.** Це модулі-склейки: вони ставляться самі, коли є їхні
   залежності. Їхня залежність від X — слабший сигнал блокування, тому в основний
   рахунок не входить, але друкується окремою колонкою.

## Колонка, якої не було в запиті review, і яка найважливіша

`unblocks_alone` — скільком застряглим модулям цей блокувальник є **єдиною**
відсутньою залежністю. Без неї «розблокує 9» читалося б як обіцянка, тоді як
залежний може чекати ще на трьох. Саме `unblocks_alone` перетворює рейтинг на
план роботи: перенеси один модуль — стільки стане переносними **того ж дня**.

Серії — параметри, а не літерали: 24.09 та сама таблиця будується для 19.0 → 20.0
зміною аргументів.
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

SQL = """
WITH core AS (SELECT name FROM core_addons WHERE series = %(base)s),
next_s AS (SELECT DISTINCT module FROM modules WHERE series = %(next)s),
base_s AS (
  SELECT repo, module, depends, coalesce(auto_install,false) AS auto_install,
         last_module_commit
  FROM modules WHERE series = %(base)s
),
edges AS (
  SELECT d.dep, m.module AS dependent, m.auto_install
  FROM base_s m, unnest(coalesce(m.depends,'{}'::text[])) AS d(dep)
  WHERE d.dep NOT IN (SELECT name FROM core)
),
stuck AS (SELECT module FROM base_s WHERE module NOT IN (SELECT module FROM next_s)),
-- скільки саме ВІДСУТНІХ на наступній серії залежностей має застряглий модуль
missing_cnt AS (
  SELECT e.dependent, count(*) AS n_missing
  FROM edges e
  WHERE NOT e.auto_install
    AND e.dependent IN (SELECT module FROM stuck)
    AND e.dep NOT IN (SELECT module FROM next_s)
  GROUP BY 1
)
SELECT e.dep AS module, m.repo,
       count(*) FILTER (WHERE NOT e.auto_install) AS needed_by,
       count(*) FILTER (WHERE NOT e.auto_install
                          AND e.dependent IN (SELECT module FROM stuck)) AS needed_by_stuck,
       count(*) FILTER (WHERE NOT e.auto_install AND mc.n_missing = 1) AS unblocks_alone,
       count(*) FILTER (WHERE e.auto_install) AS auto_install_deps,
       m.last_module_commit AS last_commit
FROM edges e
JOIN base_s m ON m.module = e.dep
LEFT JOIN missing_cnt mc ON mc.dependent = e.dependent
WHERE e.dep NOT IN (SELECT module FROM next_s)
GROUP BY e.dep, m.repo, m.last_module_commit
HAVING count(*) FILTER (WHERE NOT e.auto_install) > 0
ORDER BY unblocks_alone DESC, needed_by_stuck DESC, needed_by DESC, module
"""


def rank(conn, base="18.0", nxt="19.0", limit=None):
    cur = conn.cursor()
    cur.execute(SQL + (" LIMIT %(lim)s" if limit else ""),
                {"base": base, "next": nxt, "lim": limit})
    return cur.fetchall()


def totals(conn, base="18.0", nxt="19.0"):
    """Скільки застряглих модулів узагалі має відсутню залежність.

    Потрібне, щоб рейтинг не читався як «уся проблема — це двадцять модулів»:
    більшість застряглих не має відсутніх залежностей узагалі, тобто їх ніхто не
    тримає й перенести їх можна вже сьогодні. Це окремий, і сильніший, факт.
    """
    cur = conn.cursor()
    cur.execute("""
      WITH core AS (SELECT name FROM core_addons WHERE series = %(base)s),
      next_s AS (SELECT DISTINCT module FROM modules WHERE series = %(next)s),
      base_s AS (SELECT module, depends, coalesce(auto_install,false) AS auto_install
                 FROM modules WHERE series = %(base)s),
      stuck AS (SELECT module, depends FROM base_s
                WHERE module NOT IN (SELECT module FROM next_s)),
      blocked AS (
        SELECT s.module
        FROM stuck s, unnest(coalesce(s.depends,'{}'::text[])) AS d(dep)
        WHERE d.dep NOT IN (SELECT name FROM core)
          AND d.dep NOT IN (SELECT module FROM next_s)
        GROUP BY 1
      )
      SELECT (SELECT count(*) FROM stuck)   AS stuck_total,
             (SELECT count(*) FROM blocked) AS stuck_blocked
    """, {"base": base, "next": nxt})
    return cur.fetchone()


if __name__ == "__main__":
    from db import connect
    base = sys.argv[1] if len(sys.argv) > 1 else "18.0"
    nxt = sys.argv[2] if len(sys.argv) > 2 else "19.0"
    conn = connect()
    tt = totals(conn, base, nxt)
    print(f"{base} → {nxt}: застрягло {tt['stuck_total']}, "
          f"з них має відсутню залежність {tt['stuck_blocked']} "
          f"({100.0*tt['stuck_blocked']/max(tt['stuck_total'],1):.1f}%)")
    print(f"{'модуль':34} {'репозиторій':30} потр  застр  сам  auto  коміт")
    for r in rank(conn, base, nxt, 20):
        print(f"{r['module'][:34]:34} {r['repo'][:30]:30} "
              f"{r['needed_by']:4} {r['needed_by_stuck']:6} "
              f"{r['unblocks_alone']:4} {r['auto_install_deps']:5}  "
              f"{r['last_commit']:%Y-%m-%d}" if r['last_commit'] else "")
    conn.close()
