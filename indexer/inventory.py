#!/usr/bin/env python3
"""Інвентаризація оточення: що лежить в образі і що є ядром Odoo.

Одна робота на два місця (ops/inbox/0015 і 0016):

* похідний образ проти `env` мусить знати, чого в базовому образі бракує;
* секція залежностей на сторінці модуля мусить відрізняти «ядро Odoo» від
  «немає в індексі», інакше `base` і `account` виглядатимуть загубленими.

Обидва відповіді беруться **з самого образу**, а не з припущень: склад ядра
різний між серіями, а склад пакетів змінюється щоразу, коли образ перезбирають.
Тому всі знімки прив'язані до тегу.

    python3 indexer/inventory.py odoo:19.0 19.0
    python3 indexer/inventory.py modidx/odoo:19.0-deps-20260819 19.0
"""
import json, os, subprocess, sys
sys.path.insert(0, os.path.dirname(__file__))
from db import connect

ADDONS = "/usr/lib/python3/dist-packages/odoo/addons"


def sh_in(image, script, timeout=180):
    """Запустити shell у контейнері образу і віддати stdout."""
    r = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--entrypoint", "sh",
         image, "-c", script],
        capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise SystemExit(f"inventory: образ {image} не відповів: "
                         f"{(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def python_packages(image):
    """pip list у машинному вигляді. --format=json є в усіх сучасних pip."""
    out = sh_in(image, "pip list --format=json --disable-pip-version-check 2>/dev/null"
                       " || pip3 list --format=json --disable-pip-version-check")
    return [(p["name"], p.get("version")) for p in json.loads(out or "[]")]


def binaries(image, names):
    """Які з потрібних утиліт справді є в PATH образу.

    Перевіряємо лише те, що модулі оголосили в external_dependencies.bin:
    інвентаризувати весь PATH немає сенсу, а список оголошених — короткий.
    """
    if not names:
        return []
    # `; true` в кінці обов'язковий: якщо ЖОДНОЇ утиліти немає, останній
    # `command -v` віддає ненульовий код, і sh виходить з ним — а «нічого не
    # знайдено» це валідна відповідь, не збій. Без цього рядка інвентаризація
    # падала на образах, де немає ні cloc, ні xmlsec1.
    script = "; ".join(f'command -v {n} >/dev/null 2>&1 && echo {n}' for n in names)
    return [l.strip() for l in sh_in(image, script + "; true").splitlines() if l.strip()]


def core_addons(image):
    """Модулі, що йдуть у самому Odoo. Шлях у офіційному образі стабільний,
    але перевіряємо і запасний варіант через сам пакет odoo."""
    out = sh_in(image, f"ls {ADDONS} 2>/dev/null || "
                       f"ls $(python3 -c 'import odoo,os;print(os.path.dirname(odoo.__file__))')/addons")
    return sorted({l.strip() for l in out.splitlines() if l.strip()})


def main():
    if len(sys.argv) < 3:
        raise SystemExit("вжиток: inventory.py <образ> <серія>")
    image, series = sys.argv[1], sys.argv[2]
    conn = connect()
    cur = conn.cursor()

    cur.execute("""SELECT DISTINCT dep FROM modules,
                   jsonb_array_elements_text(ext_deps->'bin') dep
                   WHERE ext_deps ? 'bin'""")
    want_bin = [r["dep"] for r in cur.fetchall()]

    pkgs = python_packages(image)
    bins = binaries(image, want_bin)
    core = core_addons(image)

    cur.execute("DELETE FROM image_packages WHERE image_tag=%s", (image,))
    for name, ver in pkgs:
        cur.execute("""INSERT INTO image_packages (image_tag, kind, name, version)
                       VALUES (%s,'python',%s,%s)
                       ON CONFLICT (image_tag, kind, name) DO UPDATE
                         SET version=EXCLUDED.version, taken_at=now()""",
                    (image, name.lower(), ver))
    for name in bins:
        cur.execute("""INSERT INTO image_packages (image_tag, kind, name, version)
                       VALUES (%s,'bin',%s,NULL)
                       ON CONFLICT (image_tag, kind, name) DO UPDATE
                         SET taken_at=now()""", (image, name))

    cur.execute("DELETE FROM core_addons WHERE series=%s", (series,))
    for name in core:
        cur.execute("""INSERT INTO core_addons (series, name, image_tag)
                       VALUES (%s,%s,%s) ON CONFLICT (series, name) DO UPDATE
                         SET image_tag=EXCLUDED.image_tag, taken_at=now()""",
                    (series, name, image))

    print(f"{image} · серія {series}: python-пакетів {len(pkgs)}, "
          f"бінарників з оголошених {len(bins)}/{len(want_bin)}, "
          f"модулів ядра {len(core)}", file=sys.stderr)
    conn.close()


if __name__ == "__main__":
    main()
