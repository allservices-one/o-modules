#!/usr/bin/env python3
"""Генератор статики. Ні Node, ні Hugo — щоб на 4 vCPU нічого не зжирало ресурс.

Робить у var/site:
  index.html            табло міграції (головна сторінка проєкту)
  m/<repo>/<module>/     сторінка модуля зі статусом по серіях і хвостом логу
  r/<repo>/              сторінка репозиторію
  modules.json           індекс для пошуку в браузері (без сервера)
  data/*.csv             відкритий датасет
  llms.txt, methodology.html
"""
import csv, html, json, os, pathlib, sys, datetime
sys.path.insert(0, os.path.dirname(__file__))
from db import connect, ROOT

SITE = ROOT / "var" / "site"
NOW = datetime.datetime.now(datetime.timezone.utc)
BASE = os.environ.get("SITE_BASE", "https://allservices.one")
TITLE = os.environ.get("SITE_TITLE", "Module Health Index")

STATUS = {
    "ok":      ("✓", "Встановлюється",        "good"),
    "warn":    ("!", "Із попередженнями",     "warning"),
    "dep":     ("▲", "Блокує залежність",     "serious"),
    "env":     ("~", "Проблема середовища",   "muted"),
    "fail":    ("✗", "Помилка install",       "critical"),
    "timeout": ("⏱", "Таймаут",               "critical"),
    None:      ("—", "Не тестовано",          "muted"),
}

CSS = """
:root{--s:#fcfcfb;--p:#f9f9f7;--i:#0b0b0b;--i2:#52514e;--m:#898781;--l:#e1e0d9;--ax:#c3c2b7;
--good:#0ca30c;--warning:#fab219;--serious:#ec835a;--critical:#d03b3b;--a:#2a78d6}
@media(prefers-color-scheme:dark){:root{--s:#1a1a19;--p:#0d0d0d;--i:#fff;--i2:#c3c2b7;--m:#898781;
--l:#2c2c2a;--ax:#383835;--a:#3987e5}}
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
.bar{display:flex;gap:2px;height:24px;border-radius:5px;overflow:hidden;margin:10px 0 6px}
.bar div{display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;color:#fff}
.lg{display:flex;flex-wrap:wrap;gap:13px;font-size:12px;color:var(--i2)}
.sw{width:10px;height:10px;border-radius:2px;display:inline-block;margin-right:5px}
nav{font-size:13px;color:var(--m);margin-bottom:18px}
@media(max-width:700px){.tiles{grid-template-columns:1fr 1fr}}
"""

def page(title, body, desc="", jsonld=None, noindex=False):
    """noindex=True ставить <meta name="robots" content="noindex"> у <head>.

    Використовується для сторінок, у яких ще немає жодного install-статусу.
    Свідомо НЕ через robots.txt і НЕ через X-Robots-Tag у Caddy:
      * Disallow у robots.txt і noindex взаємно скасовуються — закритий краулер
        просто не прочитає noindex, а URL усе одно може потрапити в індекс;
      * Disallow заблокував би GPTBot, а цитованість LLM — основний канал проєкту.
    Тег зникає автоматично, як тільки в модуля з'явиться перший статус.
    """
    ld = f'<script type="application/ld+json">{json.dumps(jsonld, ensure_ascii=False)}</script>' if jsonld else ""
    ni = '<meta name="robots" content="noindex">' if noindex else ""
    return f"""<!DOCTYPE html><html lang="uk"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc)}">{ni}
<style>{CSS}</style>{ld}</head><body><div class="w">
<nav><a href="/">{TITLE}</a> · <a href="/methodology.html">методологія</a> · <a href="/data/">датасет</a></nav>
{body}
<p class="mut" style="margin-top:40px;border-top:1px solid var(--l);padding-top:14px">
Дані оновлено {NOW:%Y-%m-%d %H:%M} UTC · публікуємо факт прогону з логом, не оцінку вендора ·
<a href="/data/">CSV і JSON відкриті</a></p></div></body></html>"""


def chip(status):
    ic, label, cls = STATUS.get(status, STATUS[None])
    return f'<span class="c {cls}"><span>{ic}</span>{label}</span>'


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


def build():
    conn = connect()
    rows, series = fetch(conn)
    if not rows:
        print("немає даних: спершу harvest.py"); return

    mods = {}
    for r in rows:
        mods.setdefault((r["repo"], r["module"]), {})[r["series"]] = r

    newest = series[-1] if series else None
    prev = series[-2] if len(series) > 1 else None
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "data").mkdir(exist_ok=True)

    # ---------- головна: табло ----------
    present = {s: sum(1 for v in mods.values() if s in v) for s in series}
    gap = [k for k, v in mods.items() if prev in v and newest not in v] if prev else []
    ported = [k for k, v in mods.items() if prev in v and newest in v] if prev else []
    counts = {}
    for k, v in mods.items():
        st = (v.get(newest) or {}).get("status")
        counts[st] = counts.get(st, 0) + 1
    tested = sum(c for s, c in counts.items() if s)

    tiles = "".join(
        f'<div class="t"><div class="k">Модулів на {s}</div><div class="v">{present[s]}</div>'
        f'<div class="n">{"остання серія" if s == newest else ""}</div></div>'
        for s in series[-3:])
    if prev:
        pct = (len(ported) / max(1, present[prev])) * 100
        tiles += (f'<div class="t"><div class="k">Перенесено {prev}→{newest}</div>'
                  f'<div class="v">{pct:.1f}%</div><div class="n">{len(gap)} ще ні</div></div>')

    rowsh = []
    for k, v in sorted(mods.items(), key=lambda x: (x[0][0], x[0][1]))[:300]:
        repo, mod = k
        cells = "".join(f"<td>{chip((v.get(s) or {}).get('status'))}</td>" for s in series[-4:])
        rowsh.append(f'<tr><td><a href="/m/{repo}/{mod}/">{mod}</a> '
                     f'<span class="mut">{repo}</span></td>{cells}</tr>')
    head = "".join(f"<th>{s}</th>" for s in series[-4:])

    body = f"""<h1>{TITLE}</h1>
<p class="mut">Кожен публічний модуль OCA встановлюється у чисту базу кожної серії Odoo.
Публікуємо результат прогону, а не заяви вендорів.</p>
<div class="tiles">{tiles}</div>
<input id="q" placeholder="Пошук модуля або репозиторію…" autocomplete="off">
<h2>Статус на {newest}</h2>
<div class="bar">{"".join(
  f'<div style="flex:{c};background:var(--{STATUS.get(s, STATUS[None])[2]})">{c}</div>'
  for s, c in sorted(counts.items(), key=lambda x: -x[1]) if s)}</div>
<div class="lg">{"".join(
  f'<span><i class="sw" style="background:var(--{STATUS.get(s, STATUS[None])[2]})"></i>'
  f'{STATUS.get(s, STATUS[None])[0]} {STATUS.get(s, STATUS[None])[1]} — {c}</span>'
  for s, c in sorted(counts.items(), key=lambda x: -x[1]) if s)}</div>
<p class="mut">Протестовано {tested} з {len(mods)} модулів.</p>
<h2>Модулі</h2>
<table id="tbl"><tr><th>Модуль</th>{head}</tr>{''.join(rowsh)}</table>
<p class="mut">Показано перші 300. Повний перелік — у <a href="/data/">датасеті</a>.</p>
<script>
let idx=null;
document.getElementById('q').addEventListener('input',async e=>{{
  const q=e.target.value.trim().toLowerCase();
  if(!idx) idx=await (await fetch('/modules.json')).json();
  const tb=document.getElementById('tbl');
  const hits=q?idx.filter(m=>m.m.includes(q)||m.r.includes(q)).slice(0,300):idx.slice(0,300);
  tb.innerHTML='<tr><th>Модуль</th>{head}</tr>'+hits.map(m=>
    '<tr><td><a href="/m/'+m.r+'/'+m.m+'/">'+m.m+'</a> <span class="mut">'+m.r+'</span></td>'+
    m.s.map(x=>'<td>'+x+'</td>').join('')+'</tr>').join('');
}});
</script>"""
    (SITE / "index.html").write_text(page(TITLE, body,
        f"Фактична сумісність модулів OCA з версіями Odoo: {present.get(newest,0)} модулів на {newest}."))

    # ---------- сторінки модулів ----------
    search = []
    for (repo, mod), v in mods.items():
        d = SITE / "m" / repo / mod
        d.mkdir(parents=True, exist_ok=True)
        cells = []
        for s in series:
            r = v.get(s)
            if not r:
                cells.append(f"<tr><td>{s}</td><td>{chip(None)}</td><td class='mut'>гілки немає</td></tr>")
                continue
            when = r["run_at"].strftime("%Y-%m-%d") if r.get("run_at") else "—"
            det = html.escape(r.get("detail") or "")
            cells.append(f"<tr><td>{s}</td><td>{chip(r.get('status'))}</td>"
                         f"<td>{det} <span class='mut'>· прогін {when}</span></td></tr>")
        logs = ""
        for s in reversed(series):
            r = v.get(s) or {}
            if r.get("log_tail"):
                logs = f"<h2>Лог прогону {s}</h2><pre>{html.escape(r['log_tail'])}</pre>"
                break
        b = f"""<h1>{mod}</h1><p class="mut">OCA / <a href="/r/{repo}/">{repo}</a> ·
<a href="https://github.com/OCA/{repo}">джерело на GitHub</a></p>
<table><tr><th>Серія</th><th>Статус</th><th>Деталі</th></tr>{''.join(cells)}</table>{logs}"""
        ld = {"@context": "https://schema.org", "@type": "SoftwareSourceCode",
              "name": mod, "codeRepository": f"https://github.com/OCA/{repo}",
              "applicationCategory": "Odoo module",
              "url": f"{BASE}/m/{repo}/{mod}/", "dateModified": NOW.isoformat()}
        # жодного install-статусу по жодній серії → сторінка ще порожня, noindex
        has_status = any((v.get(s) or {}).get("status") for s in series)
        (d / "index.html").write_text(page(f"{mod} — сумісність з версіями Odoo", b,
            f"Чи встановлюється модуль {mod} з {repo} на версії Odoo.", ld,
            noindex=not has_status))
        search.append({"r": repo, "m": mod,
                       "s": [chip((v.get(s) or {}).get("status")) for s in series[-4:]]})

    (SITE / "modules.json").write_text(json.dumps(search, ensure_ascii=False, separators=(",", ":")))

    # ---------- сторінки репозиторіїв ----------
    byrepo = {}
    for (repo, mod), v in mods.items():
        byrepo.setdefault(repo, []).append((mod, v))
    for repo, items in byrepo.items():
        d = SITE / "r" / repo
        d.mkdir(parents=True, exist_ok=True)
        rws = "".join(
            f'<tr><td><a href="/m/{repo}/{m}/">{m}</a></td>' +
            "".join(f"<td>{chip((v.get(s) or {}).get('status'))}</td>" for s in series[-4:]) + "</tr>"
            for m, v in sorted(items))
        # те саме правило, що й для сторінок модулів: якщо в репозиторії жоден
        # модуль ще не має статусу — сторінка порожня по суті, тримаємо її поза індексом
        repo_has_status = any((v.get(s) or {}).get("status")
                              for _, v in items for s in series)
        (d / "index.html").write_text(page(f"{repo} — сумісність модулів",
            f'<h1>{repo}</h1><p class="mut">{len(items)} модулів · '
            f'<a href="https://github.com/OCA/{repo}">GitHub</a></p>'
            f'<table><tr><th>Модуль</th>{head}</tr>{rws}</table>',
            f"Сумісність модулів репозиторію OCA {repo} з версіями Odoo.",
            noindex=not repo_has_status))

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
    (SITE / "data" / "index.html").write_text(page("Датасет",
        '<h1>Відкритий датасет</h1><p>Оновлюється щодня.</p><ul>'
        '<li><a href="/data/modules.csv">modules.csv</a> — модуль × серія × статус × причина</li>'
        '<li><a href="/modules.json">modules.json</a> — індекс пошуку</li></ul>'
        '<p class="mut">Ліцензія даних: CC BY 4.0. Посилайтеся на джерело.</p>',
        "Відкриті дані про сумісність модулів OCA з версіями Odoo."))

    # ---------- robots.txt і sitemap.xml ----------
    # Нічого не забороняємо: GPTBot та інші LLM-краулери — основний канал проєкту.
    # Тонкі сторінки тримаються поза індексом посторінковим noindex (див. page()).
    (SITE / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {BASE}/sitemap.xml\n")

    # У sitemap — тільки сторінки з реальним вмістом. Сторінки під noindex у
    # sitemap не включаємо: заявляти в карті те, що просимо не індексувати, —
    # суперечливий сигнал.
    sm = ["/", "/methodology.html", "/data/"]
    (SITE / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"<url><loc>{BASE}{u}</loc>"
                  f"<lastmod>{NOW:%Y-%m-%d}</lastmod></url>\n" for u in sm)
        + "</urlset>\n")

    # ---------- llms.txt ----------
    # Формулювання залежить від того, чи є вже прогони. Заявляти «за результатами
    # реальних install-прогонів» при tested=0 — неправда, і саме цей файл цитують LLM.
    if tested:
        what = ("Фактична сумісність модулів Odoo (OCA) з версіями Odoo, "
                "за результатами реальних install-прогонів.")
    else:
        what = (f"Індекс наявності версійних гілок модулів OCA, зібраний з git. "
                f"Фактична перевірка install у процесі, протестовано {tested} з {len(mods)}.")
    (SITE / "llms.txt").write_text(f"""# {TITLE}
{what}
Дані: {BASE}/data/modules.csv (CSV, CC BY 4.0)
Сторінка модуля: {BASE}/m/<repo>/<module>/
Методологія: {BASE}/methodology.html
Останнє оновлення: {NOW.isoformat()}
Модулів в індексі: {len(mods)}. Протестовано: {tested}. Серії: {', '.join(series)}.
""")

    (SITE / "methodology.html").write_text(page("Методологія", f"""<h1>Методологія</h1>
<p>Кожен модуль встановлюється в <b>чисту базу</b> відповідної серії Odoo з офіційного образу
<code>odoo:&lt;серія&gt;</code>, без демо-даних, командою <code>-i &lt;module&gt; --stop-after-init</code>.
Результат — код виходу процесу і лог.</p>
<h2>Статуси</h2><table><tr><th>Статус</th><th>Значення</th></tr>
{''.join(f'<tr><td>{chip(k)}</td><td>{v[1]}</td></tr>' for k, v in STATUS.items() if k)}</table>
<h2>Що ми окремо НЕ вважаємо несумісністю</h2>
<p>Падіння через відсутній зовнішній python-пакет або системну утиліту в образі позначається як
«проблема середовища» і <b>не</b> зараховується модулю як несумісність. Це навмисно: інакше
статистика була б неправдивою.</p>
<h2>Обмеження</h2><ul>
<li>Платні модулі без ліцензії не встановлюються — для них публікуються лише метадані.</li>
<li>Install-прогін не є тестом функціональності: модуль може встановитися і працювати неправильно.</li>
<li>Батч-режим: при масовому проході модулі ставляться групами, при падінні групи кожен
перевіряється окремо. У даних це позначено полем <code>batched</code>.</li></ul>
<p>Публікуємо факт прогону з датою і логом, а не оцінку якості вендора.</p>""",
        "Як саме перевіряється сумісність модулів Odoo."))

    print(f"згенеровано: {len(mods)} модулів, {len(byrepo)} репозиторіїв → {SITE}")
    conn.close()


if __name__ == "__main__":
    build()
