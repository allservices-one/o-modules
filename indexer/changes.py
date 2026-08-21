#!/usr/bin/env python3
"""Матеріалізація змін стану — джерело для Atom-фідів.

Подією є **зміна**, а не прогін. Щоденний прохід дає тисячі прогонів, більшість
з яких повторюють учорашній результат; якби кожен ішов у фід, читати його було
б неможливо.

Працює інкрементально від курсора: віконна функція по всій `runs` щогодини —
марна робота, коли нових прогонів десяток.

**Сівба.** Найперший відомий стан пари (модуль, серія) записується з
`seeded=true` і у фіди не потрапляє. Без цього перший підписник отримав би
кілька тисяч записів «новий: verified» і відписався б — класичний спосіб
зіпсувати фід у день запуску.

**Стенд.** Друга причина не публікувати зміну: змінилися МИ, а не модуль.
Між двома послідовними прогонами могли поїхати образ (`runs.odoo_image`) або
правила класифікатора (`runs.rules_version`) — тоді різниця в статусі наша, і
рядок іде з `bench=true`, повз стрічки. Спіймано ззовні (ops/inbox/0019 A):
перезбірка образу 18.0 з відсутніми python-пакетами дала 41 запис `env → ok`,
а правка правила атрибуції `warn` із 0018 — ще 5. Публічно це читалося як
«модуль став сумісним 20 серпня», чого не було: жоден із 46 модулів не
змінився, `head_sha` у всіх той самий.

Обидва прапорці лишають рядок у таблиці, бо на ньому тримається порівняння
«попередній стан» для наступного прогону. Фільтрує їх лише запит фіда.
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from db import connect, SERIES
from state import derive_state

BATCH = int(os.environ.get("CHANGES_BATCH", "5000"))


def main():
    t0 = time.time()
    conn = connect()
    cur = conn.cursor()

    cur.execute("SELECT last_run_id, seeded_at FROM feed_cursor WHERE one")
    row = cur.fetchone() or {}
    cursor = row.get("last_run_id") or 0
    seeded_at = row.get("seeded_at")

    # Сівба триває, ПОКИ НЕ ЗАКІНЧИВСЯ перший масовий прохід, а не «поки не
    # відпрацював цей скрипт уперше». Різниця принципова: під час першого
    # проходу тисячі модулів отримують свій найперший прогін, і кожен з них —
    # це `prev is None`. Якби сівба закінчувалась одразу, решта проходу залила
    # б фід тими самими тисячами записів, від яких ми й захищаємось.
    cur.execute("SELECT count(*) c FROM jobs WHERE state IN ('queued','running')")
    queue_left = cur.fetchone()["c"]
    seeding = seeded_at is None or queue_left > 0

    total = bench = 0
    while True:
        # LATERAL — попередній прогін цієї ж пари (модуль, серія): його стенд
        # порівнюємо зі стендом поточного. Саме попередній ПРОГІН, а не прогін
        # позаду попередньої зміни: зміну стану ми й виявляємо між двома
        # послідовними прогонами, тому й стенд треба порівнювати той самий.
        cur.execute("""
            SELECT r.id, r.module_id, r.series, r.status, r.created_at,
                   r.odoo_image, r.rules_version,
                   m.availability, m.installable,
                   p.id AS prev_run, p.odoo_image AS prev_image,
                   p.rules_version AS prev_rules
            FROM runs r JOIN modules m ON m.id = r.module_id
            LEFT JOIN LATERAL (
                SELECT q.id, q.odoo_image, q.rules_version FROM runs q
                WHERE q.module_id = r.module_id AND q.series = r.series
                  AND q.id < r.id
                ORDER BY q.id DESC LIMIT 1
            ) p ON true
            WHERE r.id > %s
            ORDER BY r.id
            LIMIT %s
        """, (cursor, BATCH))
        runs = cur.fetchall()
        if not runs:
            break

        for r in runs:
            # Стан рахуємо тією самою функцією, що й сайт. Вісі availability
            # та installable беремо ПОТОЧНІ: історії цих полів ми не тримаємо,
            # і вигадувати її заднім числом було б гірше, ніж не мати.
            new_state, new_status = derive_state({
                "availability": r["availability"],
                "installable": r["installable"],
                "status": r["status"],
                "in_scope": r["series"] in SERIES,
            })
            cur.execute("""
                SELECT state_new, status_new FROM state_changes
                WHERE module_id=%s AND series=%s ORDER BY at DESC, id DESC LIMIT 1
            """, (r["module_id"], r["series"]))
            prev = cur.fetchone()

            if prev and prev["state_new"] == new_state and prev["status_new"] == new_status:
                cursor = r["id"]
                continue                      # нічого не змінилось — не подія

            # Стенд поїхав між двома прогонами → зміна наша, не модуля.
            # NULL порівнюється як значення: у прогонів до появи rules_version
            # вона порожня, тому перший прохід після цієї правки чесно
            # позначиться як зміна стенду. Це навмисно консервативно.
            stand = (r["prev_run"] is not None
                     and (r["prev_image"], r["prev_rules"])
                         != (r["odoo_image"], r["rules_version"]))
            if stand:
                bench += 1

            cur.execute("""
                INSERT INTO state_changes
                  (module_id, series, state_old, state_new, status_old, status_new,
                   run_id, at, seeded, bench)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id) DO NOTHING
            """, (r["module_id"], r["series"],
                  prev["state_new"] if prev else None,
                  new_state,
                  prev["status_new"] if prev else None,
                  new_status,
                  r["id"], r["created_at"],
                  seeding or prev is None, stand))
            total += 1
            cursor = r["id"]

        cur.execute("UPDATE feed_cursor SET last_run_id=%s WHERE one", (cursor,))

    # Позначаємо сівбу закінченою лише коли черга порожня: з цього моменту
    # перший прогін новоз'явленого модуля стає нормальною подією фіда.
    if seeding and queue_left == 0:
        cur.execute("UPDATE feed_cursor SET seeded_at = now() WHERE one")
        print("  сівбу завершено: черга порожня, далі зміни йдуть у фіди",
              file=sys.stderr)

    cur.execute("SELECT count(*) c FROM state_changes "
                "WHERE NOT seeded AND NOT bench")
    live = cur.fetchone()["c"]
    print(f"змін записано: {total} (сівба: {'так' if seeding else 'ні'}, "
          f"зміни стенду: {bench}, у черзі {queue_left}) · у фідах: {live} · "
          f"курсор: {cursor} · {time.time()-t0:.1f}s",
          file=sys.stderr)
    conn.close()


if __name__ == "__main__":
    main()
