#!/usr/bin/env python3
"""Зріз екосистеми OCA: які версійні гілки існують, які в них модулі, коли останній коміт.
Тільки git — ні GitHub API, ні токенів. Час роботи ~2 хв на 184 репозиторії.

Запускати щодня по таймеру. Саме цей скрипт дає цифри для публічного табло.
"""
import ast, csv, json, os, pathlib, re, shutil, subprocess, sys, tempfile, time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))
from db import ROOT, connect

SERIES = os.environ.get("HARVEST_SERIES", "16.0 17.0 18.0 19.0 20.0").split()
NOT_MODULE_DIRS = {"setup", "docs", ".github", ".gitea", "tests", "template"}
# Модуль Odoo — це тека верхнього рівня, в якій ЛЕЖИТЬ __manifest__.py.
# Анкер обов'язковий: без нього порахувалися б вкладені манифести з тестових
# фікстур (у частині репозиторіїв OCA вони є в tests/), а це знову неправда,
# тільки в інший бік. Ті самі правила, що й у bin/sync_repos.sh — списки модулів
# у БД і в пулі адонів мусять збігатися, інакше runner отримує фантоми.
MANIFEST_AT_TOP = re.compile(r"^([^/]+)/__manifest__\.py$")
# Список репозиторіїв живе В GIT, не у var/: кожна зміна екосистеми стає діффом
# у комміті, і поява чи зникнення репозиторію OCA — подія в історії, а не
# невидимість. Це заодно дані, яких більше ні в кого немає.
LIST = ROOT / "data" / "oca_repos.txt"
LIST_LEGACY = ROOT / "var" / "oca_repos.txt"        # кеш зі старої схеми

# Службові репозиторії OCA: інструменти, шаблони, дзеркала — модулів не містять.
SKIP_REPOS = {
    "maintainer-tools", "OCB", "OpenUpgrade", "openupgradelib", "pylint-odoo",
    "odoo-module-migrator", "oca-port", "oca-ci", "oca-github-bot", "oca-custom",
    ".github", "ansible-odoo", "odoo-community.org", "odoorpc", "oca-decorators",
    "odoo-pre-commit-hooks", "odoo-sentinel", "odoo-sphinx-autodoc",
    "maintainer-quality-tools", "oca-addons-repo-template", "mirrors-flake8",
    "contribute-md-template", "oca.recipe.odoo", "oca-weblate-deployment",
    "connector-magento-php-extension", "odoo-test-helper", "repo-maintainer",
    "repo-maintainer-conf", "module-composition-analysis",
}
# Якщо новий список коротший за кеш більш ніж на стільки — не застосовувати.
# Той самий принцип, що в жниві: масова втрата — це майже завжди наш збій.
LIST_SHRINK_MAX = int(os.environ.get("LIST_SHRINK_MAX", "5"))


def _github_org_repos(org="OCA", pages=10):
    """Перелік репозиторіїв організації. 3 запити на добу, БЕЗ токена.

    Джерело істини саме API, бо старий шлях (tools/repos_with_ids.txt у
    OCA/maintainer-tools) в апстрімі зник, і кеш через це відстав на 56
    репозиторіїв — чверть екосистеми. Сама OCA теж перейшла на API
    (gh.repositories_by("OCA") у tools/oca_projects.py), тобто це канонічний
    спосіб, а не обхід. Анонімний ліміт 60 запитів/год, нам треба 3 на добу.
    """
    out, seen_page = [], 0
    for page in range(1, pages + 1):
        url = f"https://api.github.com/orgs/{org}/repos?per_page=100&page={page}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "modidx-harvest",
            "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
        if not data:
            break
        seen_page += 1
        for repo in data:
            # fork і archived відсіюємо тут, а не потім: форк організації — це
            # чуже дзеркало, архів — заморожений код, у якому гілок не буває.
            if repo.get("archived") or repo.get("fork"):
                continue
            out.append(repo["name"])
    if seen_page == 0:
        raise RuntimeError("GitHub API повернув порожній перший аркуш")
    return out


def _read_list(path):
    if path.exists() and path.stat().st_size:
        return sorted({l.strip() for l in path.read_text().splitlines() if l.strip()})
    return []


def repo_names():
    """Список репозиторіїв OCA: API — джерело істини, git-кеш — аварійний фолбек."""
    cached = _read_list(LIST) or _read_list(LIST_LEGACY)

    try:
        fresh = sorted(set(_github_org_repos()) - SKIP_REPOS)
    except Exception as e:
        # Ніколи не індексувати підмножину мовчки: саме так і з'явилися ті 56.
        if not cached:
            raise SystemExit(f"harvest: GitHub API недоступний ({e}) і кешу немає. "
                             f"Відновіть {LIST} з git.")
        print(f"  !! GitHub API недоступний ({e}) — працюю з КЕШУ {LIST.name}, "
              f"{len(cached)} репозиторіїв. Список може відставати.", file=sys.stderr)
        return cached

    if cached:
        gone = sorted(set(cached) - set(fresh))
        added = sorted(set(fresh) - set(cached))
        if len(cached) - len(fresh) > LIST_SHRINK_MAX:
            print(f"  !! список ЗУПИНЕНО: було {len(cached)}, стало {len(fresh)} "
                  f"(−{len(cached)-len(fresh)}, межа −{LIST_SHRINK_MAX}). "
                  f"Працюю з кешу, список не оновлюю.", file=sys.stderr)
            for n in gone[:20]:
                print(f"     зник: {n}", file=sys.stderr)
            return cached
        # Новий репозиторій індексуємо, але НАЗИВАЄМО: інакше службовий одного дня
        # тихо потрапить у статистику як «модулі».
        for n in added:
            print(f"  + новий репозиторій OCA: {n}", file=sys.stderr)
        for n in gone:
            print(f"  − зник зі списку OCA: {n}", file=sys.stderr)

    LIST.parent.mkdir(parents=True, exist_ok=True)
    LIST.write_text("\n".join(fresh) + "\n")
    return fresh


def remote_branches(repo):
    r = sh(["git", "ls-remote", "--heads", f"https://github.com/OCA/{repo}"], timeout=120)
    if r.returncode != 0:
        return None
    return {l.split("refs/heads/")[-1] for l in r.stdout.splitlines() if "refs/heads/" in l}


def branch_detail(repo, series):
    """Модулі гілки + sha теки кожного модуля + дата останнього коміта.
    Treeless shallow clone: ~1 с і ~200 KB на гілку."""
    d = WORK / f"{repo}@{series}"
    r = sh(["git", "clone", "-q", "--filter=blob:none", "--no-checkout", "--depth", "1",
            "--single-branch", "--branch", series,
            f"https://github.com/OCA/{repo}", str(d)], timeout=240)
    if r.returncode != 0:
        return None
    # Два запити до одного клону: нерекурсивний дає sha тек, рекурсивний —
    # хто з них справді модуль. `-r` на treeless-клоні дотягує всі дерева
    # (не блоби), тому дорожчий за кореневий ls-tree — ціна зміряна в outbox.
    t = sh(["git", "ls-tree", "HEAD"], cwd=d)
    if t.returncode != 0:
        shutil.rmtree(d, ignore_errors=True)
        return None
    trees = {}
    for line in t.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[1] == "tree":
            name = parts[3]
            if name in NOT_MODULE_DIRS or name.startswith("."):
                continue
            trees[name] = parts[2]          # sha теки модуля
    r = sh(["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=d, timeout=300)
    if r.returncode != 0:
        # R2: успішний клон ще не означає прочитану гілку. Якщо ls-tree впав,
        # ми НЕ знаємо складу гілки — а порожній список нижче виглядав би як
        # «гілка спорожніла» і жнець викосив би весь репозиторій із індексу.
        shutil.rmtree(d, ignore_errors=True)
        return None
    with_manifest = set()
    for line in r.stdout.splitlines():
        m = MANIFEST_AT_TOP.match(line)
        if m:
            with_manifest.add(m.group(1))
    mods = {n: sha for n, sha in trees.items() if n in with_manifest}
    last = sh(["git", "log", "-1", "--format=%cI"], cwd=d).stdout.strip()
    shutil.rmtree(d, ignore_errors=True)
    return {"modules": mods, "last_commit": last}


def handle(repo):
    brs = remote_branches(repo)
    if brs is None:
        return {"repo": repo, "error": "ls-remote failed"}
    row = {"repo": repo}
    for s in SERIES:
        if s in brs:
            d = branch_detail(repo, s)
            # ok=False — клон не вдався. Це НЕ те саме, що «гілка без модулів»:
            # без цього прапорця жнець нижче викосив би всі модулі репозиторію
            # через одну мережеву помилку.
            # ok=False — гілку прочитати не вдалося (клон АБО будь-який ls-tree).
            # Це НЕ те саме, що «гілка без модулів»: без цього прапорця жнець
            # викосив би всі модулі репозиторію через одну мережеву помилку.
            row[s] = {"modules": (d or {}).get("modules", {}),
                      "last_commit": (d or {}).get("last_commit", ""),
                      "ok": d is not None}
        else:
            row[s] = None
    return row


# Версія методики підрахунку модулів. Змінюється РАЗОМ із правилом, і цифри
# різних методик не можна класти в один ряд для розрахунку нахилу.
#   v1 — будь-яка тека верхнього рівня (до 19.08.2026)
#   v2 — тека з __manifest__.py на першому рівні
METHOD = os.environ.get("HARVEST_METHOD", "v2")

# R1: жнець — єдине місце, яке ВТРАЧАЄ дані, і його помилка тиха: індекс худне,
# а сайт виглядає нормально. Тому верхня межа за прогін. Реальна робота OCA за
# добу — одиниці рядків, тож поріг не заважає ніколи, крім справжньої аварії.
REAP_MAX_SHARE = 0.02
REAP_MIN_ABS = 50


def reap(cur, targets):
    """Прибрати з індексу модулі, яких у гілці більше немає.

    Без цього harvest лише додає: виправлене правило перестало б додавати
    фантоми, але вже наявні лишилися б у БД назавжди. І гірше — модуль,
    прибраний з апстріму, показувався б вічно, що для індексу «фактичного
    стану» є прямим дефектом.

    Косимо ТІЛЬКИ по парах (репозиторій, серія), прочитаних цього разу без
    помилки: недоступний на хвилину GitHub не має права зітерти пів індексу.
    Спершу рахуємо заплановане, і лише потім видаляємо — щоб перевищення
    порога зупинило жнива цілком, а не на середині.
    """
    cur.execute("SELECT count(*) c FROM modules")
    total = cur.fetchone()["c"]
    limit = max(REAP_MIN_ABS, int(total * REAP_MAX_SHARE))

    plan, warn = [], []
    for repo, s, keep, branch_gone in targets:
        if branch_gone:
            cur.execute("SELECT count(*) c FROM modules WHERE repo=%s AND series=%s",
                        (repo, s))
            n = cur.fetchone()["c"]
            if n:
                plan.append((repo, s, None, n))
            continue
        cur.execute("SELECT count(*) c FROM modules WHERE repo=%s AND series=%s",
                    (repo, s))
        had = cur.fetchone()["c"]
        if had and not keep:
            # R2: гілка не спорожнюється в нуль за добу — це майже завжди наш
            # збій, а не робота OCA. Попереджаємо, але не косимо.
            warn.append(f"{repo}@{s}: у БД {had}, у зрізі 0 — не чіпаю")
            continue
        cur.execute("SELECT count(*) c FROM modules WHERE repo=%s AND series=%s "
                    "AND NOT (module = ANY(%s))", (repo, s, keep or [""]))
        n = cur.fetchone()["c"]
        if n:
            plan.append((repo, s, keep, n))

    for w in warn:
        print(f"  ! жнець: {w}", file=sys.stderr)

    planned = sum(n for *_, n in plan)
    if planned > limit:
        print(f"  !! жнець ЗУПИНЕНО: заплановано видалити {planned} рядків "
              f"при межі {limit} ({REAP_MAX_SHARE:.0%} від {total}). "
              f"Нічого не видалено — розбиратися вручну.", file=sys.stderr)
        for repo, s, _, n in sorted(plan, key=lambda x: -x[3])[:10]:
            print(f"     {repo}@{s}: {n}", file=sys.stderr)
        return 0

    done = 0
    for repo, s, keep, _ in plan:
        if keep is None:
            cur.execute("DELETE FROM modules WHERE repo=%s AND series=%s", (repo, s))
        else:
            cur.execute("DELETE FROM modules WHERE repo=%s AND series=%s "
                        "AND NOT (module = ANY(%s))", (repo, s, keep or [""]))
        done += cur.rowcount
    if done:
        print(f"  жнець: прибрано записів модулів: {done} (межа {limit})", file=sys.stderr)
    return done


def persist(rows):
    conn = connect()
    cur = conn.cursor()
    for r in rows:
        if r.get("error"):
            continue
        for s in SERIES:
            b = r.get(s)
            if not b:
                continue
            for mod, sha in b["modules"].items():
                cur.execute("""
                    INSERT INTO modules (repo, module, series, head_sha, last_commit, seen_at)
                    VALUES (%s,%s,%s,%s, NULLIF(%s,'')::timestamptz, now())
                    ON CONFLICT (repo, module, series) DO UPDATE
                      SET head_sha = EXCLUDED.head_sha,
                          last_commit = EXCLUDED.last_commit,
                          seen_at = now()
                """, (r["repo"], mod, s, sha, b["last_commit"]))
    targets = []
    for r in rows:
        if r.get("error"):
            continue
        for s in SERIES:
            b = r.get(s)
            if b is None:                       # гілки більше немає в OCA
                targets.append((r["repo"], s, None, True))
            elif b.get("ok"):                   # гілку реально прочитано
                targets.append((r["repo"], s, list(b["modules"].keys()), False))
    reap(cur, targets)

    for s in SERIES:
        repos = sum(1 for r in rows if r.get(s))
        mods = sum(len(r[s]["modules"]) for r in rows if r.get(s))
        # R3: PRIMARY KEY (taken_at, series) при taken_at DEFAULT now() не
        # конфліктує НІКОЛИ, тому старий ON CONFLICT DO NOTHING не працював і
        # кожен ручний запуск додавав ще одну точку за той самий день —
        # публічний графік темпу став би зубчастим від наших же перевірок.
        # Тепер конфлікт по (день, серія, метод): повторний запуск оновлює точку.
        cur.execute("""INSERT INTO series_snapshots (series, repos, modules, method)
                       VALUES (%s,%s,%s,%s)
                       ON CONFLICT (((taken_at AT TIME ZONE 'UTC')::date), series, method)
                       DO UPDATE
                         SET repos = EXCLUDED.repos, modules = EXCLUDED.modules,
                             taken_at = now()""",
                    (s, repos, mods, METHOD))
    conn.close()


def main():
    t0 = time.time()
    repos = repo_names()
    print(f"репозиторіїв OCA: {len(repos)}", file=sys.stderr)
    rows = []
    with ThreadPoolExecutor(max_workers=int(os.environ.get("HARVEST_WORKERS", "10"))) as ex:
        for i, r in enumerate(ex.map(handle, repos)):
            rows.append(r)
            if (i + 1) % 25 == 0:
                print(f"  {i+1}/{len(repos)} · {time.time()-t0:.0f}s", file=sys.stderr)

    out = ROOT / "var"
    out.mkdir(parents=True, exist_ok=True)
    (out / "oca_snapshot.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))

    with open(out / "oca_modules.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["repo", "module"] + SERIES)
        pairs = {}
        for r in rows:
            for s in SERIES:
                for m in (r.get(s) or {}).get("modules", {}):
                    pairs.setdefault((r["repo"], m), {x: 0 for x in SERIES})[s] = 1
        for (repo, m), v in sorted(pairs.items()):
            w.writerow([repo, m] + [v[s] for s in SERIES])

    try:
        persist(rows)
    except Exception as e:                       # база може бути ще не піднята
        print(f"  (у БД не записано: {e})", file=sys.stderr)

    print(f"\nготово за {time.time()-t0:.0f}s", file=sys.stderr)
    for s in SERIES:
        print(f"  {s:>5}: репозиторіїв {sum(1 for r in rows if r.get(s)):>3}"
              f" · модулів {sum(len(r[s]['modules']) for r in rows if r.get(s)):>5}", file=sys.stderr)
    shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    main()
