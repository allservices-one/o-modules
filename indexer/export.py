#!/usr/bin/env python3
"""Генератор статики. Ні Node, ні Hugo — щоб на 4 vCPU нічого не зжирало ресурс.

Двомовний: англійська за замовчуванням у корені, українська під /uk/.

Робить у var/site:
  index.html  /  uk/index.html              табло міграції
  m/<repo>/<module>/  /  uk/m/...           сторінка модуля
  r/<repo>/  /  uk/r/...                    сторінка репозиторію
  methodology.html  /  uk/methodology.html
  data/index.html  /  uk/data/index.html
  data/modules.csv                          датасет, один на обидві мови
  modules.json                              індекс пошуку, мовно-нейтральний
  robots.txt, sitemap.xml, llms.txt         одні, англійською, у корені
  favicon.svg                               власний знак

Логотипів Odoo і OCA не використовуємо — ні їхніх зображень, ні похідних.
Візуальна мова — версійні чипи і власний знак. У підвалі кожної сторінки
стоїть дисклеймер про непов'язаність із Odoo S.A. та OCA.
"""
import csv, html, json, os, pathlib, shutil, subprocess, sys, datetime
sys.path.insert(0, os.path.dirname(__file__))
from db import connect, ROOT

SITE = ROOT / "var" / "site"
NOW = datetime.datetime.now(datetime.timezone.utc)
BASE = os.environ.get("SITE_BASE", "https://allservices.one")
TITLE = os.environ.get("SITE_TITLE", "Module Health Index")

LANGS = ("en", "uk")
DEFAULT_LANG = "en"

# Константи, які не виводяться з даних.
NEXT_SERIES = "20.0"                             # серія, гілок якої ще нема
V20_KEYNOTE = datetime.date(2026, 9, 24)         # кейноут Odoo Experience
V19_RELEASED = datetime.date(2025, 9, 1)         # для «N місяців після релізу 19.0»

# Палітра статусів install. НЕ використовувати для смуги розриву портування:
# ці кольори означають результат прогону, а «не перенесено» — не помилка.
STATUS_CLS = {
    "ok": ("✓", "good"), "warn": ("!", "warning"), "dep": ("▲", "serious"),
    "env": ("~", "muted"), "fail": ("✗", "critical"), "timeout": ("⏱", "critical"),
    None: ("—", "muted"),
}

T = {
    "en": {
        "lang_name": "EN", "other_name": "УК",
        "nav_methodology": "methodology", "nav_dataset": "dataset",
        "h1": "Which OCA modules actually run on which Odoo version",
        "sub": ("We install every public OCA module into a clean database of every Odoo "
                "series and publish what happened — the install log, with a date. "
                "Not vendor claims."),
        "why_exists_h": "Why this exists",
        "why_exists_p": ("Odoo ships a major version every year. Third-party and custom "
                         "modules are not covered by Odoo's own upgrade service, and nobody "
                         "publishes whether a given module actually installs on a given "
                         "version. So partners find out during the upgrade, by hand."),
        "why_now_h": "Why now",
        "why_now_p": ("Odoo {next} is unveiled on {keynote}. As of today, {gap} of the "
                      "{base} modules available on {prev} have never been ported to {new} "
                      "— {months} months after {new} shipped. {zero} repositories have a "
                      "{next} branch yet."),
        "who_h": "Who it's for",
        "who_p": ("Odoo partners scoping an upgrade, OCA maintainers, and anyone choosing "
                  "which modules to build on."),
        "not_h": "What this is not",
        "not_p": ("An install check, not a functional test — a module can install and still "
                  "misbehave. Paid modules cannot be installed without a licence, so for "
                  "those we publish metadata only, clearly marked. Failures caused by a "
                  "missing Python package in the image are reported as environment "
                  "problems, never as version incompatibility."),
        "search": "Search a module or repository…",
        "tile_modules_on": "Modules on {s}", "tile_latest": "latest series",
        "tile_ported": "Ported {a}→{b}", "tile_still": "{n} not yet",
        "gap_h": "Porting gap {a} → {b}",
        "gap_ported": "Ported to {b}", "gap_not": "Not ported",
        "status_h": "Install status on {s}",
        "tested": "Tested {n} of {m} modules.",
        "modules_h": "Modules", "col_module": "Module",
        "showing": 'Showing the first 300. Full list in the <a href="{dataset}">dataset</a>.',
        "m_series": "Series", "m_status": "Status", "m_details": "Details",
        "m_nobranch": "no branch", "m_run": "run {d}", "m_log": "Install log {s}",
        "m_source": "source on GitHub", "m_in": "in",
        "r_modules": "{n} modules",
        "d_h1": "Open dataset",
        "d_intro": "Module × series × status × cause. Licence: CC BY 4.0. Please cite the source.",
        "d_csv": "modules.csv — module × series × status × cause",
        "d_json": "modules.json — search index",
        "meth_h1": "Methodology",
        "footer_updated": "Data updated {d} UTC",
        "footer_fact": "we publish the install log with a date, not a vendor rating",
        "footer_open": "CSV and JSON are open",
        "independent": ("Independent project. Not affiliated with Odoo S.A. or the "
                        "Odoo Community Association."),
        "st_ok": "Installs", "st_warn": "Installs with warnings",
        "st_dep": "Blocked by a dependency", "st_env": "Environment problem",
        "st_fail": "Install error", "st_timeout": "Timed out", "st_none": "Not tested",
    },
    "uk": {
        "lang_name": "УК", "other_name": "EN",
        "nav_methodology": "методологія", "nav_dataset": "датасет",
        "h1": "Які модулі OCA справді працюють на якій версії Odoo",
        "sub": ("Ми встановлюємо кожен публічний модуль OCA у чисту базу кожної серії "
                "Odoo і публікуємо, що сталося — лог install і дату. Не заяви вендорів."),
        "why_exists_h": "Чому це існує",
        "why_exists_p": ("Odoo випускає мажорну версію щороку. Сторонні й кастомні модулі "
                         "офіційна служба апгрейду не мігрує, і ніде не публікується, чи "
                         "конкретний модуль узагалі встановлюється на конкретну версію. "
                         "Партнери дізнаються це під час апгрейду, вручну."),
        "why_now_h": "Чому зараз",
        "why_now_p": ("Odoo {next} презентують {keynote}. Станом на сьогодні {gap} із "
                      "{base} модулів, доступних на {prev}, так і не перенесені на {new} "
                      "— через {months} місяців після релізу {new}. Гілки {next} не має "
                      "{zero} репозиторій."),
        "who_h": "Для кого",
        "who_p": ("Партнери Odoo, які планують апгрейд, мейнтейнери OCA, і будь-хто, "
                  "хто вибирає модулі під проєкт."),
        "not_h": "Чим це не є",
        "not_p": ("Перевірка install, а не функціональний тест — модуль може встановитися "
                  "й працювати неправильно. Платні модулі без ліцензії не встановити, для "
                  "них публікуємо лише метадані й позначаємо це явно. Падіння через "
                  "відсутній python-пакет в образі позначається як проблема середовища, "
                  "а не як несумісність із версією."),
        "search": "Пошук модуля або репозиторію…",
        "tile_modules_on": "Модулів на {s}", "tile_latest": "остання серія",
        "tile_ported": "Перенесено {a}→{b}", "tile_still": "{n} ще ні",
        "gap_h": "Розрив портування {a} → {b}",
        "gap_ported": "Перенесені на {b}", "gap_not": "Не перенесені",
        "status_h": "Статус install на {s}",
        "tested": "Протестовано {n} з {m} модулів.",
        "modules_h": "Модулі", "col_module": "Модуль",
        "showing": 'Показано перші 300. Повний перелік — у <a href="{dataset}">датасеті</a>.',
        "m_series": "Серія", "m_status": "Статус", "m_details": "Деталі",
        "m_nobranch": "гілки немає", "m_run": "прогін {d}", "m_log": "Лог прогону {s}",
        "m_source": "джерело на GitHub", "m_in": "у",
        "r_modules": "{n} модулів",
        "d_h1": "Відкритий датасет",
        "d_intro": "Модуль × серія × статус × причина. Ліцензія: CC BY 4.0. Посилайтеся на джерело.",
        "d_csv": "modules.csv — модуль × серія × статус × причина",
        "d_json": "modules.json — індекс пошуку",
        "meth_h1": "Методологія",
        "footer_updated": "Дані оновлено {d} UTC",
        "footer_fact": "публікуємо факт прогону з логом і датою, не оцінку вендора",
        "footer_open": "CSV і JSON відкриті",
        "independent": ("Незалежний проєкт. Не пов'язаний з Odoo S.A. чи Odoo Community "
                        "Association."),
        "st_ok": "Встановлюється", "st_warn": "Із попередженнями",
        "st_dep": "Блокує залежність", "st_env": "Проблема середовища",
        "st_fail": "Помилка install", "st_timeout": "Таймаут", "st_none": "Не тестовано",
    },
}

METHODOLOGY = {
    "en": """<h2>How a module is checked</h2>
<p>Every module is installed into a <b>clean database</b> of the matching Odoo series, built
from the official <code>odoo:&lt;series&gt;</code> image, with no demo data, using
<code>-i &lt;module&gt; --stop-after-init</code>. The result is the process exit code and the log.</p>
<h2>Statuses</h2>{table}
<h2>What we deliberately do NOT count as incompatibility</h2>
<p>A failure caused by a missing external Python package or a missing system utility in the
image is recorded as an <b>environment problem</b> and is <b>not</b> counted against the
module. The same applies when the harness itself fails to expose the module to Odoo. This is
deliberate: without that separation the statistics would be untrue, and an untrue index is
worth nothing.</p>
<h2>Limits</h2><ul>
<li>Paid modules cannot be installed without a licence — for those we publish metadata only.</li>
<li>An install check is not a functional test: a module can install and still misbehave.</li>
<li>Batch mode: on a mass pass modules are installed in groups; if a group fails, each member
is retried alone. The dataset records this in the <code>batched</code> field.</li></ul>
<p>We publish the fact of a run, with its date and log — not a rating of a vendor.</p>""",
    "uk": """<h2>Як перевіряється модуль</h2>
<p>Кожен модуль встановлюється в <b>чисту базу</b> відповідної серії Odoo з офіційного образу
<code>odoo:&lt;серія&gt;</code>, без демо-даних, командою <code>-i &lt;module&gt; --stop-after-init</code>.
Результат — код виходу процесу і лог.</p>
<h2>Статуси</h2>{table}
<h2>Що ми свідомо НЕ вважаємо несумісністю</h2>
<p>Падіння через відсутній зовнішній python-пакет або системну утиліту в образі позначається як
<b>проблема середовища</b> і <b>не</b> зараховується модулю. Так само — якщо сам харнес не подав
модуль до Odoo. Це навмисно: без такого розділення статистика була б неправдивою, а неправдивий
індекс не вартий нічого.</p>
<h2>Обмеження</h2><ul>
<li>Платні модулі без ліцензії не встановлюються — для них публікуються лише метадані.</li>
<li>Install-прогін не є тестом функціональності: модуль може встановитися і працювати неправильно.</li>
<li>Батч-режим: при масовому проході модулі ставляться групами, при падінні групи кожен
перевіряється окремо. У даних це позначено полем <code>batched</code>.</li></ul>
<p>Публікуємо факт прогону з датою і логом, а не оцінку якості вендора.</p>""",
}

# Власний знак: три смуги спадної довжини — рівно та історія, що й у даних.
# Не містить і не наслідує символіку Odoo чи OCA.
MARK = ('<svg class="mk" viewBox="0 0 24 24" aria-hidden="true">'
        '<rect x="2" y="3" width="20" height="18" rx="4" fill="none" '
        'stroke="currentColor" stroke-width="2"/>'
        '<rect x="6" y="7"  width="12" height="2.4" rx="1.2" fill="currentColor"/>'
        '<rect x="6" y="11" width="8"  height="2.4" rx="1.2" fill="currentColor"/>'
        '<rect x="6" y="15" width="4"  height="2.4" rx="1.2" fill="currentColor"/>'
        '</svg>')

FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
<rect width="24" height="24" rx="5" fill="#0b0b0b"/>
<rect x="5" y="6"  width="14" height="2.6" rx="1.3" fill="#fcfcfb"/>
<rect x="5" y="10.7" width="9" height="2.6" rx="1.3" fill="#2a78d6"/>
<rect x="5" y="15.4" width="4.5" height="2.6" rx="1.3" fill="#c3c2b7"/>
</svg>
"""

CSS = """
:root{--s:#fcfcfb;--p:#f9f9f7;--i:#0b0b0b;--i2:#52514e;--m:#898781;--l:#e1e0d9;--ax:#c3c2b7;
--good:#0ca30c;--warning:#fab219;--serious:#ec835a;--critical:#d03b3b;--a:#2a78d6;
--ported:#2a78d6;--notported:#c3c2b7}
@media(prefers-color-scheme:dark){:root{--s:#1a1a19;--p:#0d0d0d;--i:#fff;--i2:#c3c2b7;--m:#898781;
--l:#2c2c2a;--ax:#383835;--a:#3987e5;--ported:#3987e5;--notported:#4a4a46}}
*{box-sizing:border-box}body{margin:0;background:var(--p);color:var(--i);
font:15px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}
.w{max-width:980px;margin:0 auto;padding:28px 18px 72px}
a{color:var(--a);text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:26px;letter-spacing:-.02em;margin:0 0 4px}h2{font-size:18px;margin:34px 0 8px}
.mut{color:var(--m);font-size:13px}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--l);
border:1px solid var(--l);border-radius:8px;overflow:hidden;margin:16px 0 22px}
.t{background:var(--s);padding:12px 13px}.t .k{font-size:11px;text-transform:uppercase;
letter-spacing:.04em;color:var(--m)}.t .v{font-size:24px;font-weight:600;letter-spacing:-.02em}
.t .n{font-size:11.5px;color:var(--m)}
table{width:100%;border-collapse:collapse;font-size:13.5px;background:var(--s)}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--m);
padding:8px 10px;border-bottom:1px solid var(--ax)}
td{padding:8px 10px;border-bottom:1px solid var(--l);font-variant-numeric:tabular-nums}
.c{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;font-weight:600;white-space:nowrap}
.good{color:var(--good)}.warning{color:var(--warning)}.serious{color:var(--serious)}
.critical{color:var(--critical)}.muted{color:var(--m)}
pre{background:var(--l);padding:11px 13px;border-radius:7px;overflow-x:auto;
font:11.5px/1.6 ui-monospace,Menlo,monospace;color:var(--i2)}
input{width:100%;padding:11px 13px;font-size:14px;border:1px solid var(--ax);border-radius:7px;
background:var(--s);color:var(--i)}
.bar{display:flex;gap:2px;height:26px;border-radius:5px;overflow:hidden;margin:10px 0 6px}
.bar div{display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;
color:#fff;min-width:2px}
.lg{display:flex;flex-wrap:wrap;gap:13px;font-size:12px;color:var(--i2)}
.sw{width:10px;height:10px;border-radius:2px;display:inline-block;margin-right:5px}
nav{display:flex;align-items:center;gap:10px;font-size:13px;color:var(--m);margin-bottom:18px;
flex-wrap:wrap}
nav .sp{margin-left:auto}
.brand{display:inline-flex;align-items:center;gap:7px;color:var(--i);font-weight:600}
.brand:hover{text-decoration:none}
.mk{width:19px;height:19px;flex:none}
.vchip{display:inline-block;padding:1px 7px;border:1px solid var(--ax);border-radius:20px;
font-size:11.5px;font-weight:600;color:var(--i2);font-variant-numeric:tabular-nums}
.lead{font-size:16px;color:var(--i2);margin:0 0 6px;max-width:70ch}
.sec{max-width:74ch}.sec h2{font-size:15px;margin:22px 0 4px}
.sec p{margin:0;color:var(--i2);font-size:14px}
.ind{color:var(--m);font-size:12px;margin-top:10px}
@media(max-width:700px){.tiles{grid-template-columns:1fr 1fr}}
"""


def loc(lang, path):
    """Публічний URL сторінки. path завжди починається з '/'."""
    return path if lang == DEFAULT_LANG else "/uk" + path


def out_path(lang, rel):
    return (SITE if lang == DEFAULT_LANG else SITE / "uk") / rel


def st_label(status, lang):
    key = {"ok": "st_ok", "warn": "st_warn", "dep": "st_dep", "env": "st_env",
           "fail": "st_fail", "timeout": "st_timeout"}.get(status, "st_none")
    return T[lang][key]


def chip(status, lang):
    ic, cls = STATUS_CLS.get(status, STATUS_CLS[None])
    return f'<span class="c {cls}"><span>{ic}</span>{st_label(status, lang)}</span>'


def page(lang, url, title, body, desc="", jsonld=None, noindex=False):
    """Сторінка з canonical на себе і hreflang на обидві мови.

    noindex=True ставить <meta name="robots" content="noindex"> — для сторінок,
    у яких ще немає жодного install-статусу. Свідомо НЕ через robots.txt і НЕ
    через заголовок у Caddy: Disallow і noindex взаємно скасовуються (закритий
    краулер не прочитає noindex), а Disallow заблокував би GPTBot, тобто основний
    канал проєкту. Правило однакове в обох мовних деревах.
    """
    t = T[lang]
    ld = f'<script type="application/ld+json">{json.dumps(jsonld, ensure_ascii=False)}</script>' if jsonld else ""
    ni = '<meta name="robots" content="noindex">' if noindex else ""
    other = "uk" if lang == "en" else "en"
    alts = "".join(
        f'<link rel="alternate" hreflang="{lg}" href="{BASE}{loc(lg, url)}">' for lg in LANGS
    ) + f'<link rel="alternate" hreflang="x-default" href="{BASE}{loc(DEFAULT_LANG, url)}">'
    return f"""<!DOCTYPE html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc)}">{ni}
<link rel="canonical" href="{BASE}{loc(lang, url)}">{alts}
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<style>{CSS}</style>{ld}</head><body><div class="w">
<nav><a class="brand" href="{loc(lang, '/')}">{MARK}{TITLE}</a>
<a href="{loc(lang, '/methodology.html')}">{t['nav_methodology']}</a>
<a href="{loc(lang, '/data/')}">{t['nav_dataset']}</a>
<span class="sp"></span><a href="{loc(other, url)}" hreflang="{other}">{T[other]['lang_name']}</a></nav>
{body}
<p class="mut" style="margin-top:40px;border-top:1px solid var(--l);padding-top:14px">
{t['footer_updated'].format(d=f'{NOW:%Y-%m-%d %H:%M}')} · {t['footer_fact']} ·
<a href="{loc(lang, '/data/')}">{t['footer_open']}</a></p>
<p class="ind">{t['independent']}</p></div></body></html>"""


def _cmd(args, default=""):
    """Тихо: status.json не має падати через відсутній git чи docker."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else default
    except Exception:
        return default


def status_json(conn):
    """Машинний зріз стану сервера → var/site/status.json.

    Це канал, яким сесія без SSH бачить, що тут відбувається: харвест, черга,
    прогони, версії образів. Публічний і без секретів — ні паролів, ні
    внутрішніх шляхів, ні IP. Caddy віддає його з Cache-Control: no-store,
    інакше читач бачив би вчорашній стан і робив із нього хибні висновки.
    """
    cur = conn.cursor()
    cur.execute("SELECT series, count(*) c FROM modules GROUP BY 1 ORDER BY 1")
    by_series = {r["series"]: r["c"] for r in cur.fetchall()}
    cur.execute("SELECT taken_at, method FROM series_snapshots "
                "ORDER BY taken_at DESC LIMIT 1")
    row = cur.fetchone() or {}
    last_harvest, method = row.get("taken_at"), row.get("method")
    cur.execute("SELECT status, count(*) c FROM latest_runs GROUP BY 1 ORDER BY 1")
    by_status = {r["status"]: r["c"] for r in cur.fetchall()}
    cur.execute("SELECT state, count(*) c FROM jobs GROUP BY 1 ORDER BY 1")
    queue = {r["state"]: r["c"] for r in cur.fetchall()}
    cur.execute("SELECT count(*) c FROM modules")
    total_modules = cur.fetchone()["c"]

    images = {}
    for s in ("18.0", "19.0", "20.0"):
        img = _cmd(["docker", "image", "inspect", f"odoo:{s}",
                    "--format", "{{.Id}} {{.Created}}"])
        if img:
            iid, _, created = img.partition(" ")
            images[s] = {"id": iid[:19], "created": created.strip()[:19]}

    du = shutil.disk_usage("/")
    mem_available_mb = None
    try:
        for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                mem_available_mb = int(line.split()[1]) // 1024
                break
    except Exception:
        pass

    data = {
        "generated_at": NOW.isoformat(),
        "commit": _cmd(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], "unknown"),
        "harvest": {
            "last_run": last_harvest.isoformat() if last_harvest else None,
            # Версія методики підрахунку. Цифри різних методик не можна класти
            # в один ряд — саме тому вона їде разом із ними, а не десь у доках.
            "method": method,
            "modules_by_series": by_series,
        },
        "runs": {
            "by_status": by_status,
            "tested": sum(by_status.values()),
            "total_modules": total_modules,
        },
        "queue": {k: queue.get(k, 0) for k in ("queued", "running", "error")},
        "images": images,
        "disk_free_gb": round(du.free / 1024**3, 1),
        "mem_available_mb": mem_available_mb,
    }
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "status.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    return data


def fetch(conn):
    cur = conn.cursor()
    cur.execute("""
      SELECT m.repo, m.module, m.series, m.head_sha, m.last_commit,
             r.status, r.cause, r.detail, r.log_tail, r.created_at AS run_at, r.duration_ms
      FROM modules m
      LEFT JOIN latest_runs r ON r.module_id = m.id
      ORDER BY m.repo, m.module, m.series
    """)
    rows = cur.fetchall()
    cur.execute("SELECT DISTINCT series FROM modules ORDER BY series")
    series = [r["series"] for r in cur.fetchall()]
    return rows, series


def home(lang, series, mods, present, ported, gap, counts, tested, repos_next):
    t = T[lang]
    newest = series[-1]
    prev = series[-2] if len(series) > 1 else None

    tiles = "".join(
        f'<div class="t"><div class="k">{t["tile_modules_on"].format(s=s)}</div>'
        f'<div class="v">{present[s]}</div>'
        f'<div class="n">{t["tile_latest"] if s == newest else ""}</div></div>'
        for s in series[-3:])
    if prev:
        pct = (len(ported) / max(1, present[prev])) * 100
        tiles += (f'<div class="t"><div class="k">{t["tile_ported"].format(a=prev, b=newest)}</div>'
                  f'<div class="v">{pct:.1f}%</div>'
                  f'<div class="n">{t["tile_still"].format(n=len(gap))}</div></div>')

    # Смуга: поки прогонів немає — розрив портування, а не статуси install.
    # Кольори нейтральні: «не перенесено» не є помилкою, тому статусна палітра
    # (зелений/жовтий/червоний) тут не використовується.
    if tested:
        seg = "".join(
            f'<div style="flex:{c};background:var(--{STATUS_CLS.get(s, STATUS_CLS[None])[1]})">{c}</div>'
            for s, c in sorted(counts.items(), key=lambda x: -x[1]) if s)
        leg = "".join(
            f'<span><i class="sw" style="background:var(--{STATUS_CLS.get(s, STATUS_CLS[None])[1]})"></i>'
            f'{STATUS_CLS.get(s, STATUS_CLS[None])[0]} {st_label(s, lang)} — {c}</span>'
            for s, c in sorted(counts.items(), key=lambda x: -x[1]) if s)
        bar_h = t["status_h"].format(s=newest)
    else:
        np_, p_ = len(gap), len(ported)
        seg = (f'<div style="flex:{p_};background:var(--ported)">{p_}</div>'
               f'<div style="flex:{np_};background:var(--notported)">{np_}</div>')
        leg = (f'<span><i class="sw" style="background:var(--ported)"></i>'
               f'→ {t["gap_ported"].format(b=newest)} — {p_}</span>'
               f'<span><i class="sw" style="background:var(--notported)"></i>'
               f'— {t["gap_not"]} — {np_}</span>')
        bar_h = t["gap_h"].format(a=prev, b=newest)

    months = (NOW.year - V19_RELEASED.year) * 12 + (NOW.month - V19_RELEASED.month)
    zero = ("Zero" if lang == "en" else "жодний") if not repos_next else str(repos_next)
    why_now = t["why_now_p"].format(
        next=NEXT_SERIES, keynote=(f"{V20_KEYNOTE:%d %B %Y}" if lang == "en"
                                   else f"{V20_KEYNOTE.day} вересня {V20_KEYNOTE.year}"),
        gap=f"{len(gap):,}".replace(",", " "), base=f"{present[prev]:,}".replace(",", " "),
        prev=prev, new=newest, months=months, zero=zero)

    chips = " ".join(f'<span class="vchip">{s}</span>' for s in series)
    head = "".join(f"<th>{s}</th>" for s in series[-4:])
    rowsh = []
    for (repo, mod), v in sorted(mods.items())[:300]:
        cells = "".join(f"<td>{chip((v.get(s) or {}).get('status'), lang)}</td>" for s in series[-4:])
        rowsh.append(f'<tr><td><a href="{loc(lang, f"/m/{repo}/{mod}/")}">{mod}</a> '
                     f'<span class="mut">{repo}</span></td>{cells}</tr>')

    chipmap = json.dumps({(k or ""): chip(k, lang) for k in STATUS_CLS}, ensure_ascii=False)
    body = f"""<h1>{t['h1']}</h1>
<p class="lead">{t['sub']}</p>
<p>{chips}</p>
<div class="tiles">{tiles}</div>
<div class="sec">
<h2>{t['why_exists_h']}</h2><p>{t['why_exists_p']}</p>
<h2>{t['why_now_h']}</h2><p>{why_now}</p>
<h2>{t['who_h']}</h2><p>{t['who_p']}</p>
<h2>{t['not_h']}</h2><p>{t['not_p']}</p>
</div>
<h2>{bar_h}</h2>
<div class="bar">{seg}</div>
<div class="lg">{leg}</div>
<p class="mut">{t['tested'].format(n=tested, m=len(mods))}</p>
<h2>{t['modules_h']}</h2>
<input id="q" placeholder="{t['search']}" autocomplete="off">
<table id="tbl"><tr><th>{t['col_module']}</th>{head}</tr>{''.join(rowsh)}</table>
<p class="mut">{t['showing'].format(dataset=loc(lang, '/data/'))}</p>
<script>
const CH={chipmap}, PFX="{'' if lang == DEFAULT_LANG else '/uk'}";
let idx=null;
document.getElementById('q').addEventListener('input',async e=>{{
  const q=e.target.value.trim().toLowerCase();
  if(!idx) idx=await (await fetch('/modules.json')).json();
  const hits=q?idx.filter(m=>m.m.includes(q)||m.r.includes(q)).slice(0,300):idx.slice(0,300);
  document.getElementById('tbl').innerHTML=
    '<tr><th>{t['col_module']}</th>{head}</tr>'+hits.map(m=>
    '<tr><td><a href="'+PFX+'/m/'+m.r+'/'+m.m+'/">'+m.m+'</a> <span class="mut">'+m.r+'</span></td>'+
    m.s.map(x=>'<td>'+(CH[x||""]||"")+'</td>').join('')+'</tr>').join('');
}});
</script>"""
    return page(lang, "/", f"{TITLE} — {t['h1']}", body,
                t["sub"][:180])


def build():
    conn = connect()
    # Пишемо ПЕРШИМ і до перевірки на порожні дані: якщо даних нема, саме це
    # й треба показати назовні, а не мовчати.
    status_json(conn)
    rows, series = fetch(conn)
    if not rows:
        print("немає даних: спершу harvest.py"); return

    mods = {}
    for r in rows:
        mods.setdefault((r["repo"], r["module"]), {})[r["series"]] = r

    newest = series[-1]
    prev = series[-2] if len(series) > 1 else None
    present = {s: sum(1 for v in mods.values() if s in v) for s in series}
    gap = [k for k, v in mods.items() if prev in v and newest not in v] if prev else []
    ported = [k for k, v in mods.items() if prev in v and newest in v] if prev else []
    counts = {}
    for v in mods.values():
        st = (v.get(newest) or {}).get("status")
        counts[st] = counts.get(st, 0) + 1
    tested = sum(c for s, c in counts.items() if s)
    repos_next = len({r for (r, _), v in mods.items() if NEXT_SERIES in v})

    for lang in LANGS:
        out_path(lang, "data").mkdir(parents=True, exist_ok=True)

    # ---------- головна ----------
    for lang in LANGS:
        out_path(lang, "index.html").write_text(
            home(lang, series, mods, present, ported, gap, counts, tested, repos_next))

    # ---------- сторінки модулів ----------
    search = []
    for (repo, mod), v in mods.items():
        has_status = any((v.get(s) or {}).get("status") for s in series)
        for lang in LANGS:
            t = T[lang]
            cells = []
            for s in series:
                r = v.get(s)
                if not r:
                    cells.append(f'<tr><td><span class="vchip">{s}</span></td>'
                                 f"<td>{chip(None, lang)}</td>"
                                 f"<td class='mut'>{t['m_nobranch']}</td></tr>")
                    continue
                when = r["run_at"].strftime("%Y-%m-%d") if r.get("run_at") else "—"
                det = html.escape(r.get("detail") or "")
                cells.append(f'<tr><td><span class="vchip">{s}</span></td>'
                             f"<td>{chip(r.get('status'), lang)}</td>"
                             f"<td>{det} <span class='mut'>· {t['m_run'].format(d=when)}</span></td></tr>")
            logs = ""
            for s in reversed(series):
                r = v.get(s) or {}
                if r.get("log_tail"):
                    logs = (f"<h2>{t['m_log'].format(s=s)}</h2>"
                            f"<pre>{html.escape(r['log_tail'])}</pre>")
                    break
            b = (f'<h1>{mod}</h1><p class="mut">{t["m_in"]} '
                 f'<a href="{loc(lang, f"/r/{repo}/")}">{repo}</a> · '
                 f'<a href="https://github.com/OCA/{repo}">{t["m_source"]}</a></p>'
                 f'<table><tr><th>{t["m_series"]}</th><th>{t["m_status"]}</th>'
                 f'<th>{t["m_details"]}</th></tr>{"".join(cells)}</table>{logs}')
            ld = {"@context": "https://schema.org", "@type": "SoftwareSourceCode",
                  "name": mod, "codeRepository": f"https://github.com/OCA/{repo}",
                  "applicationCategory": "Odoo module", "inLanguage": lang,
                  "url": f"{BASE}{loc(lang, f'/m/{repo}/{mod}/')}",
                  "dateModified": NOW.isoformat()}
            d = out_path(lang, f"m/{repo}/{mod}")
            d.mkdir(parents=True, exist_ok=True)
            title = (f"{mod} — Odoo version compatibility" if lang == "en"
                     else f"{mod} — сумісність з версіями Odoo")
            desc = (f"Does {mod} from {repo} install on each Odoo series." if lang == "en"
                    else f"Чи встановлюється модуль {mod} з {repo} на версії Odoo.")
            (d / "index.html").write_text(
                page(lang, f"/m/{repo}/{mod}/", title, b, desc, ld, noindex=not has_status))
        search.append({"r": repo, "m": mod,
                       "s": [(v.get(s) or {}).get("status") for s in series[-4:]]})

    # мовно-нейтральний індекс пошуку: коди статусів, підписи рендерить сторінка
    (SITE / "modules.json").write_text(
        json.dumps(search, ensure_ascii=False, separators=(",", ":")))

    # ---------- сторінки репозиторіїв ----------
    byrepo = {}
    for (repo, mod), v in mods.items():
        byrepo.setdefault(repo, []).append((mod, v))
    for repo, items in byrepo.items():
        repo_has_status = any((v.get(s) or {}).get("status") for _, v in items for s in series)
        for lang in LANGS:
            t = T[lang]
            head = "".join(f"<th>{s}</th>" for s in series[-4:])
            rws = "".join(
                f'<tr><td><a href="{loc(lang, f"/m/{repo}/{m}/")}">{m}</a></td>' +
                "".join(f"<td>{chip((v.get(s) or {}).get('status'), lang)}</td>"
                        for s in series[-4:]) + "</tr>"
                for m, v in sorted(items))
            d = out_path(lang, f"r/{repo}")
            d.mkdir(parents=True, exist_ok=True)
            title = (f"{repo} — module compatibility" if lang == "en"
                     else f"{repo} — сумісність модулів")
            (d / "index.html").write_text(page(
                lang, f"/r/{repo}/", title,
                f'<h1>{repo}</h1><p class="mut">{t["r_modules"].format(n=len(items))} · '
                f'<a href="https://github.com/OCA/{repo}">GitHub</a></p>'
                f'<table><tr><th>{t["col_module"]}</th>{head}</tr>{rws}</table>',
                title, noindex=not repo_has_status))

    # ---------- датасет ----------
    with open(SITE / "data" / "modules.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["repo", "module"] + [f"{s}_present" for s in series]
                   + [f"{s}_status" for s in series] + [f"{s}_cause" for s in series])
        for (repo, mod), v in sorted(mods.items()):
            w.writerow([repo, mod]
                       + [1 if s in v else 0 for s in series]
                       + [(v.get(s) or {}).get("status") or "" for s in series]
                       + [(v.get(s) or {}).get("cause") or "" for s in series])
    for lang in LANGS:
        t = T[lang]
        out_path(lang, "data").mkdir(parents=True, exist_ok=True)
        out_path(lang, "data/index.html").write_text(page(
            lang, "/data/", f"{t['d_h1']} — {TITLE}",
            f'<h1>{t["d_h1"]}</h1><p class="lead">{t["d_intro"]}</p><ul>'
            f'<li><a href="/data/modules.csv">{t["d_csv"]}</a></li>'
            f'<li><a href="/modules.json">{t["d_json"]}</a></li></ul>',
            t["d_intro"]))

    # ---------- методологія ----------
    for lang in LANGS:
        t = T[lang]
        tbl = ('<table><tr><th>' + t["m_status"] + "</th><th>"
               + ("Meaning" if lang == "en" else "Значення") + "</th></tr>"
               + "".join(f"<tr><td>{chip(k, lang)}</td><td>{st_label(k, lang)}</td></tr>"
                         for k in ("ok", "warn", "dep", "env", "fail", "timeout"))
               + "</table>")
        out_path(lang, "methodology.html").write_text(page(
            lang, "/methodology.html", f"{t['meth_h1']} — {TITLE}",
            f"<h1>{t['meth_h1']}</h1>" + METHODOLOGY[lang].format(table=tbl),
            "How module compatibility is verified." if lang == "en"
            else "Як саме перевіряється сумісність модулів Odoo."))

    # ---------- знак ----------
    (SITE / "favicon.svg").write_text(FAVICON)

    # ---------- robots.txt і sitemap.xml (одні, у корені) ----------
    # Нічого не забороняємо: GPTBot та інші LLM-краулери — основний канал проєкту.
    # Тонкі сторінки тримаються поза індексом посторінковим noindex (див. page()).
    (SITE / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {BASE}/sitemap.xml\n")

    # У sitemap — лише сторінки з реальним вмістом, по одному <url> на мову,
    # з hreflang-альтернативами. Сторінки під noindex у карту не включаємо.
    pages = ["/", "/methodology.html", "/data/"]
    urls = []
    for pth in pages:
        alt = "".join(
            f'<xhtml:link rel="alternate" hreflang="{lg}" href="{BASE}{loc(lg, pth)}"/>'
            for lg in LANGS
        ) + (f'<xhtml:link rel="alternate" hreflang="x-default" '
             f'href="{BASE}{loc(DEFAULT_LANG, pth)}"/>')
        for lg in LANGS:
            urls.append(f"<url><loc>{BASE}{loc(lg, pth)}</loc>{alt}"
                        f"<lastmod>{NOW:%Y-%m-%d}</lastmod></url>")
    (SITE / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
        ' xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
        + "\n".join(urls) + "\n</urlset>\n")

    # ---------- llms.txt (один, англійською, у корені) ----------
    # Формулювання залежить від наявності прогонів: заявляти «install runs»
    # при tested=0 — неправда, і саме цей файл цитують LLM.
    if tested:
        what = ("Which OCA modules actually install on which Odoo version, "
                "from real install runs.")
    else:
        what = (f"Index of which Odoo version branches each OCA module has, collected from "
                f"git. Actual install verification is in progress: tested {tested} "
                f"of {len(mods)}.")
    (SITE / "llms.txt").write_text(f"""# {TITLE}
{what}
English: {BASE}/ · Ukrainian: {BASE}/uk/
Data: {BASE}/data/modules.csv (CSV, CC BY 4.0)
Module page: {BASE}/m/<repo>/<module>/  (Ukrainian: {BASE}/uk/m/<repo>/<module>/)
Methodology: {BASE}/methodology.html
Last updated: {NOW.isoformat()}
Modules indexed: {len(mods)}. Tested: {tested}. Series: {', '.join(series)}.
Independent project. Not affiliated with Odoo S.A. or the Odoo Community Association.
""")

    print(f"згенеровано: {len(mods)} модулів × {len(LANGS)} мови, "
          f"{len(byrepo)} репозиторіїв → {SITE}")
    conn.close()


if __name__ == "__main__":
    build()
