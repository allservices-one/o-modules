#!/usr/bin/env python3
"""Димова перевірка образу перед тим, як пускати його в прогони.

Навіщо це існує, дослівно. 19.08.2026 похідний образ із оголошеними
залежностями транзитивно підняв `cryptography` до 50.0.0, і той зламав
системний `pyOpenSSL` 23.2.0. У такому образі падає імпорт `OpenSSL`, який
робить сам `base` Odoo — тобто **будь-який** модуль отримував `fail`. Класифікатор
чесно писав `fail/orm_api`, сторінка показала б це як несумісність модулів з
19.0, і ми б удруге звинуватили чужий код у власній помилці.

Знайшлося лише тому, що я відкрив лог. Мовчки це отруїло б увесь датасет.

Тому правило: **жоден образ не потрапляє в `series_image` без цієї перевірки.**
Вона коштує один прогін і закриває цілий клас катастроф.

    python3 indexer/smoke.py modidx/odoo:19.0-deps-20260819 19.0
    echo $?   # 0 — образ придатний
"""
import os, subprocess, sys, uuid
sys.path.insert(0, os.path.dirname(__file__))
from db import _password, ROOT

PGPASS = _password()
CHILD_ENV = dict(os.environ, PASSWORD=PGPASS, PGPASSWORD=PGPASS)


def psql(sql, db="postgres"):
    return subprocess.run(
        ["docker", "exec", "-i", "-e", "PGPASSWORD", "modidx-pg", "psql",
         "-U", "odoo", "-d", db, "-v", "ON_ERROR_STOP=1", "-t", "-A", "-c", sql],
        capture_output=True, text=True, timeout=120, env=CHILD_ENV)


def check(image, series):
    fails = []

    # 1. Найдешевша перевірка й та, що спіймала б реальну поломку: чи
    #    імпортуються модулі, без яких не завантажується сам base Odoo.
    #
    # `import odoo` тут БУЛО ДЕКОРАЦІЄЮ, і це виявилось 21.08.2026 на першій
    # живій збірці master. З 19.0 в офіційному образі немає
    # `dist-packages/odoo/__init__.py` — `odoo` став неявним namespace-пакетом
    # (PEP 420). Такий `import odoo` успішний ЗАВЖДИ, доки існує тека, навіть
    # якщо всередині немає жодного робочого файла:
    #     16.0/17.0/18.0 → __init__.py є, hasattr(odoo,'release') = True
    #     19.0 і master  → __init__.py немає, hasattr = False, import усе одно ок
    # Тобто рівно на найновішій серії — тій, за якою ми гонимось 24 вересня, —
    # сторож переставав перевіряти платформу й пропускав би будь-який образ.
    # `from odoo import release` імпортує СПРАВЖНІЙ підмодуль і працює в обох
    # випадках; заодно віддає версію, тобто доводить, що в образі саме той Odoo,
    # на який ми думаємо, що дивимось.
    r = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--entrypoint", "sh", image,
         "-c", "python3 -c 'import OpenSSL, cryptography, lxml, psycopg2, PIL;"
               " from odoo import release; print(release.version)'"],
        capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        fails.append("імпорт платформи: " + (r.stderr or r.stdout).strip()[-300:])
    else:
        print(f"   платформа: odoo {r.stdout.strip()}", file=sys.stderr)

    # 2. Справжня установка `base` у чисту БД. Дорожче, але саме вона доводить,
    #    що образ придатний: імпорт може пройти, а реєстр не зібратися.
    # Похідний образ, який нічого не поставив, — теж непридатний, хоча `base`
    # у ньому працює бездоганно: він просто дорівнює базовому. Саме так сталося
    # з 17.0 — pip 22.0.2 не знає --break-system-packages, усі 133 встановлення
    # впали, і перевірка «платформа ціла» пропустила порожній образ.
    r = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--entrypoint", "sh", image,
         "-c", "test -f /modidx/installed.txt && wc -l < /modidx/installed.txt || echo -"],
        capture_output=True, text=True, timeout=120)
    got = (r.stdout or "").strip()
    if got != "-" and got.isdigit() and int(got) == 0:
        fails.append("похідний образ не поставив жодного пакета — дорівнює базовому")

    db = "smoke_" + uuid.uuid4().hex[:10]
    psql(f'DROP DATABASE IF EXISTS {db} WITH (FORCE)')
    c = psql(f'CREATE DATABASE {db}')
    if c.returncode != 0:
        return [f"не вдалося створити БД: {c.stderr.strip()[:200]}"]
    try:
        r = subprocess.run(
            ["docker", "run", "--rm", "--network", "modidx", "--memory=2g",
             "--memory-swap", "2g", "--cpus", "1.5",
             "-e", "HOST=pg", "-e", "PORT=5432", "-e", "USER=odoo", "-e", "PASSWORD",
             image, "odoo", "-d", db, "-i", "base",
             "--without-demo=all", "--stop-after-init", "--no-http",
             "--max-cron-threads=0", "--log-level=warn"],
            capture_output=True, text=True, timeout=600, env=CHILD_ENV)
        log = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0:
            fails.append(f"install base: rc={r.returncode} · " + log.strip()[-300:])
        else:
            # rc=0 ще не доказ — той самий урок, що й з ir_module_module.
            q = psql("SELECT state FROM ir_module_module WHERE name='base'", db=db)
            if q.stdout.strip() != "installed":
                fails.append(f"base не встановлений: state={q.stdout.strip() or 'немає'}")
    finally:
        psql(f'DROP DATABASE IF EXISTS {db} WITH (FORCE)')
    return fails


def main():
    if len(sys.argv) < 3:
        raise SystemExit("вжиток: smoke.py <образ> <серія>")
    image, series = sys.argv[1], sys.argv[2]
    fails = check(image, series)
    if fails:
        print(f"✗ {image}: НЕ ПРИДАТНИЙ", file=sys.stderr)
        for f in fails:
            print(f"   {f}", file=sys.stderr)
        sys.exit(1)
    print(f"✓ {image}: base ставиться, платформа ціла", file=sys.stderr)


if __name__ == "__main__":
    main()
