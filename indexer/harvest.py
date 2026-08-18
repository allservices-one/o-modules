#!/usr/bin/env python3
"""Зріз екосистеми OCA: які версійні гілки існують, які в них модулі, коли останній коміт.
Тільки git — ні GitHub API, ні токенів. Час роботи ~2 хв на 184 репозиторії.

Запускати щодня по таймеру. Саме цей скрипт дає цифри для публічного табло.
"""
import ast, csv, json, os, pathlib, re, shutil, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))
from db import ROOT, connect

SERIES = os.environ.get("HARVEST_SERIES", "16.0 17.0 18.0 19.0 20.0").split()
NOT_MODULE_DIRS = {"setup", "docs", ".github", ".gitea", "tests", "template"}
LIST = ROOT / "var" / "oca_repos.txt"
WORK = pathlib.Path(tempfile.mkdtemp(prefix="harvest-"))


def sh(args, cwd=None, timeout=180):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def repo_names():
    if LIST.exists() and LIST.stat().st_size:
        return sorted({l.strip() for l in LIST.read_text().splitlines() if l.strip()})
    tmp = WORK / "mt"
    sh(["git", "clone", "-q", "--depth", "1",
        "https://github.com/OCA/maintainer-tools", str(tmp)])
    txt = (tmp / "tools" / "repos_with_ids.txt").read_text()
    skip = {"maintainer-tools", "OCB", "OpenUpgrade", "openupgradelib", "pylint-odoo",
            "odoo-module-migrator", "oca-port", "oca-ci", "oca-github-bot", "oca-custom",
            ".github", "ansible-odoo", "odoo-community.org", "odoorpc", "oca-decorators",
            "odoo-pre-commit-hooks", "odoo-sentinel", "odoo-sphinx-autodoc",
            "maintainer-quality-tools", "oca-addons-repo-template", "mirrors-flake8",
            "contribute-md-template", "oca.recipe.odoo", "oca-weblate-deployment",
            "connector-magento-php-extension", "odoo-test-helper"}
    names = sorted({m for m in re.findall(r"github\.com/OCA/(\S+)", txt) if m not in skip})
    LIST.parent.mkdir(parents=True, exist_ok=True)
    LIST.write_text("\n".join(names) + "\n")
    return names


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
    t = sh(["git", "ls-tree", "HEAD"], cwd=d)
    mods = {}
    for line in t.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[1] == "tree":
            name = parts[3]
            if name in NOT_MODULE_DIRS or name.startswith("."):
                continue
            mods[name] = parts[2]           # sha теки модуля
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
            d = branch_detail(repo, s) or {}
            row[s] = {"modules": d.get("modules", {}), "last_commit": d.get("last_commit", "")}
        else:
            row[s] = None
    return row


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
    for s in SERIES:
        repos = sum(1 for r in rows if r.get(s))
        mods = sum(len(r[s]["modules"]) for r in rows if r.get(s))
        cur.execute("""INSERT INTO series_snapshots (series, repos, modules)
                       VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""", (s, repos, mods))
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
