#!/usr/bin/env python3
"""IndexNow: сказати Bing і DuckDuckGo, які сторінки змінилися.

`ops/inbox/2026-08-21T1530` E. Один POST зі списком URL замість очікування
обходу. Google IndexNow **не** приймає — для нього працює лише sitemap із
`lastmod`, який у нас тепер є.

## Що саме надсилаємо

Джерело — `state_changes` із фільтром `NOT seeded AND NOT bench`, тобто рівно те,
що вважається подією для Atom-фідів: **зміна стану МОДУЛЯ**. Не «прогін
відбувся»: щоденний прохід дає тисячі прогонів, і надсилати їх усі означало б
щодня заявляти, що змінилися всі 7 000 сторінок — тобто зробити сигнал
безглуздим саме тоді, коли він потрібен (`ops/inbox/0019` A, той самий урок, що
й для фідів). `bench` відсікає різницю, яка належить нам, а не модулю: інший
образ або нова версія правил класифікатора.

## Поправка до опису в 0019/1530

«Без ключів» — не зовсім так: реєстрації немає, але ключ потрібен, і він мусить
лежати текстом на `https://<хост>/<ключ>.txt`. Це і є доказ володіння доменом.
Ключ **публічний за призначенням** — його віддає веб-сервер кожному, — тому він у
git і в `data/`, а не в `.env`. Найгірше, що дає чужий ключ: змусити краулер
обійти НАШІ ж сторінки.

## Чому за замовчуванням нічого не надсилається

`--submit` обов'язковий. Це дія назовні: ми повідомляємо третій сервіс. Скрипт
без прапорця друкує, що надіслав би, і виходить — щоб перший запуск не став
несподіванкою для нікого, і щоб його можна було безпечно поставити в таймер лише
свідомо.

    python3 indexer/indexnow.py                 # покаже, що надіслало б
    python3 indexer/indexnow.py --submit        # надішле
    python3 indexer/indexnow.py --hours 72      # інше вікно
"""
import argparse, json, os, secrets, sys, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(__file__))
from db import connect, ROOT

BASE = os.environ.get("SITE_BASE", "https://allservices.one")
SITE = ROOT / "var" / "site"
KEY_FILE = ROOT / "data" / "indexnow.key"
ENDPOINT = "https://api.indexnow.org/indexnow"
# Ліміт протоколу — 10 000 URL на запит. Ріжемо нижче: 10 000 сторінок за добу
# означало б не «зміни», а перекласифікацію всього індексу, і тоді правильна дія
# не пінг, а sitemap.
MAX_URLS = 10_000


def key():
    """Ключ створюється один раз і живе в git: він публічний за призначенням."""
    if KEY_FILE.exists():
        k = KEY_FILE.read_text().strip()
        if k:
            return k
    k = secrets.token_hex(16)
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_text(k + "\n")
    print(f"створено новий ключ IndexNow: {KEY_FILE}", file=sys.stderr)
    return k


def publish_key(k):
    """Файл-доказ у корені сайту. Пишемо на кожному запуску, а не раз:
    `var/site` може бути перезібраний, і тоді ключ мовчки перестав би
    підтверджуватись — а IndexNow відповів би 403, коли його вже ніхто не читає.
    """
    if not SITE.exists():
        print(f"немає {SITE} — спершу export.py", file=sys.stderr)
        return None
    p = SITE / f"{k}.txt"
    p.write_text(k + "\n")
    return f"{BASE}/{k}.txt"


def changed_urls(conn, hours):
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT m.repo, m.module
        FROM state_changes c JOIN modules m ON m.id = c.module_id
        WHERE NOT c.seeded AND NOT c.bench
          AND c.at > now() - (%s || ' hours')::interval
        ORDER BY 1, 2
    """, (hours,))
    rows = cur.fetchall()
    urls = []
    for r in rows:
        urls.append(f"{BASE}/m/{r['repo']}/{r['module']}/")
        urls.append(f"{BASE}/uk/m/{r['repo']}/{r['module']}/")
    return rows, urls


def submit(payload):
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read(200).decode(errors="replace")
    except urllib.error.HTTPError as e:
        # Коди протоколу говорять різне, і плутати їх дорого: 403 — ключ не
        # підтверджується (файл не віддається), 422 — URL не з нашого хоста,
        # 429 — надто часто. Жоден із них не є «надіслано».
        return e.code, (e.read(300).decode(errors="replace") or e.reason)
    except Exception as e:
        return None, str(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--submit", action="store_true", help="справді надіслати")
    ap.add_argument("--hours", type=int, default=24)
    a = ap.parse_args()

    conn = connect()
    rows, urls = changed_urls(conn, a.hours)
    conn.close()
    if not urls:
        print(f"IndexNow: за {a.hours} год змін стану модулів немає — нічого надсилати")
        return
    if len(urls) > MAX_URLS:
        print(f"IndexNow: {len(urls)} URL — це більше за ліміт {MAX_URLS}. "
              f"Такий обсяг означає перекласифікацію всього індексу, а не зміни; "
              f"правильна дія тут — sitemap, не пінг. НЕ надсилаю.")
        return

    k = key()
    loc = publish_key(k)
    payload = {"host": BASE.split("//", 1)[-1], "key": k,
               "keyLocation": loc, "urlList": urls}
    print(f"IndexNow: модулів зі зміною стану за {a.hours} год — {len(rows)}, "
          f"URL до надсилання — {len(urls)} (по дві мови)")
    for u in urls[:6]:
        print(f"   {u}")
    if len(urls) > 6:
        print(f"   … і ще {len(urls) - 6}")
    if not a.submit:
        print(f"ключ: {loc or '(сайт не згенерований)'}")
        print("це показ без надсилання — додайте --submit")
        return
    if not loc:
        print("ключ не опублікований — надсилати не можна, буде 403")
        return
    code, body = submit(payload)
    print(f"IndexNow: HTTP {code} · {body.strip()[:200]}")
    if code not in (200, 202):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
