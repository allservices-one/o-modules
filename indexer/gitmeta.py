#!/usr/bin/env python3
"""Історія модуля з чекауту: коли востаннє чіпали, як часто, і хто.

Найдешевше з усього, що можна показати на сторінці модуля, і найкорисніше:
**дата останнього коміту** поруч із «немає гілки 19.0» — це вже висновок,
а не факт. Модуль, який не змінювали три роки й не перенесли, і модуль,
який активно ведуть, але ще не перенесли, — різні історії, і читач має
бачити, яка перед ним.

Жодного зовнішнього запиту: усе з `git log` по теці в уже наявному чекауті.
Потрібна повна історія — `sync_repos.sh` клонує з `--filter=blob:none`
(дерева без вмісту файлів), бо на `--depth 1` дата коміту модуля дорівнювала б
даті клону.

Запускати після `sync_repos.sh`, поруч із `manifests.py`.
"""
import os, re, subprocess, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from db import connect, ROOT, SERIES

# Скільки авторів показувати. Більше не поміщається в рядок і не додає змісту:
# питання «хто це тримає» має відповідь із двох-трьох імен або не має її взагалі.
TOP_AUTHORS = 3

# Боти OCA торкаються КОЖНОГО модуля: переклади з Weblate, оновлення README,
# прогони pre-commit. Якщо їх рахувати, то «активний за останній рік» стає
# правдою для всієї екосистеми і перестає щось означати — на першому ж зрізі
# так вийшло 1 718 «активних» модулів із 1 868 неперенесених.
# Питання, на яке має відповідати ця цифра: чи торкався модуля ЖИВИЙ автор.
BOTS = {"oca-git-bot", "oca transbot", "weblate", "oca-ci", "dependabot",
        "dependabot[bot]", "pre-commit-ci", "pre-commit-ci[bot]",
        "github-actions", "github-actions[bot]", "oca-travis", "transbot"}


# Фільтра за іменем автора мало. Переклади з Weblate комітяться під ЖИВИМИ
# акаунтами перекладачів, і «Ed-Spain · Translated using Weblate (Spanish)»
# рахувався б як робота над кодом. Тому друга перевірка — за темою коміту:
# в OCA сувора конвенція префіксів, і вона розрізняє роботу від обслуговування
# краще за будь-яку евристику по іменах.
NOT_CODE_WORK = re.compile(
    r"^\s*(?:\[BOT\]|\[UPD\]|\[I18N\]"
    r"|Translated using Weblate|Update translation files|Added translation"
    r"|Deleted translation|Update README|pre-commit auto|\[IMP\] update dotfiles)",
    re.IGNORECASE)


def is_bot(name):
    n = name.strip().lower()
    return n in BOTS or n.endswith("[bot]") or "bot" == n.split()[-1:][0:1]


def git(repo_dir, args, timeout=60):
    r = subprocess.run(["git", "-C", str(repo_dir)] + args,
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout if r.returncode == 0 else ""


def _log(repo_dir, module, since=None):
    """→ [(автор, тема)] по комітах модуля. Один виклик замість двох shortlog."""
    args = ["log", "--no-merges", "--format=%an%x09%s"]
    if since:
        args.append(f"--since={since}")
    out = git(repo_dir, args + ["--", module], timeout=120)
    rows = []
    for line in out.splitlines():
        author, _, subject = line.partition("\t")
        if author.strip():
            rows.append((author.strip(), subject.strip()))
    return rows


def _code_work(rows):
    """Лишити тільки коміти, які є роботою над кодом: не боти й не переклади."""
    return [(a, s) for a, s in rows if not is_bot(a) and not NOT_CODE_WORK.match(s)]


def module_git(repo_dir, module):
    """→ (дата останнього коміту, людських комітів за 12 міс, топ авторів, файлів)

    Дата останнього коміту — БУДЬ-ЯКОГО, зокрема ботівського: питання «коли це
    востаннє чіпали» чесно включає переклади. А от «скільки комітів за рік» і
    «хто тримає» рахуються лише по людях, інакше обидві цифри вимірюють
    активність інфраструктури OCA, а не модуля.
    """
    last = git(repo_dir, ["log", "-1", "--format=%cI", "--", module]).strip()
    n12 = len(_code_work(_log(repo_dir, module, since="12.months")))
    counts = {}
    for a, _ in _code_work(_log(repo_dir, module)):
        counts[a] = counts.get(a, 0) + 1
    authors = [a for a, _ in sorted(counts.items(), key=lambda kv: -kv[1])][:TOP_AUTHORS]
    files = git(repo_dir, ["ls-tree", "-r", "--name-only", "HEAD", "--", module])
    files = len([l for l in files.splitlines() if l.strip()])
    return last or None, n12, authors or None, files or None


def main():
    t0 = time.time()
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT id, repo, module, series FROM modules WHERE series = ANY(%s) "
                "ORDER BY series, repo, module", (SERIES,))
    rows = cur.fetchall()
    print(f"модулів: {len(rows)} (серії {', '.join(SERIES)})", file=sys.stderr)

    stats = {"ok": 0, "no_checkout": 0, "no_history": 0}
    seen_repo = None
    for i, r in enumerate(rows):
        repo_dir = ROOT / "var" / "repos" / r["series"] / r["repo"]
        if not (repo_dir / ".git").exists():
            stats["no_checkout"] += 1
            continue
        # Дешева перевірка раз на репозиторій: на shallow-чекауті дата коміту
        # модуля дорівнює даті клону, і мовчки писати таке в БД гірше, ніж
        # не писати нічого — воно виглядає як справжня дата.
        key = (r["series"], r["repo"])
        if key != seen_repo:
            seen_repo = key
            shallow = git(repo_dir, ["rev-parse", "--is-shallow-repository"]).strip()
            repo_ok = shallow != "true"
        if not repo_ok:
            stats["no_history"] += 1
            continue

        last, n12, authors, files = module_git(repo_dir, r["module"])
        cur.execute("""UPDATE modules SET last_module_commit=%s, commits_12m=%s,
                         top_authors=%s, files_count=%s, git_at=now() WHERE id=%s""",
                    (last, n12, authors, files, r["id"]))
        stats["ok"] += 1
        if (i + 1) % 500 == 0:
            print(f"  {i+1}/{len(rows)} · {time.time()-t0:.0f}s", file=sys.stderr)

    print(f"\nготово за {time.time()-t0:.0f}s", file=sys.stderr)
    for k, v in stats.items():
        print(f"  {k:14} {v}", file=sys.stderr)
    conn.close()


if __name__ == "__main__":
    main()
