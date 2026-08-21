#!/usr/bin/env python3
"""Вартовий гілки наступної серії. Сигнал для нас — git, а не кейноут.

Кейноут Odoo Experience 24.09.2026 08:30 CEST (06:30 UTC) підтверджений квитком
(ops/inbox/0022), але трансляції немає, і власник не їде. Тому чекати новин,
дивитися YouTube чи перезапускати harvest руками 24-го — це способи проґавити
вікно. Гілка `20.0` з'явиться в публічному git тоді, коли з'явиться, і ловити
треба саме цю подію.

Три перевірки, від найдешевшої:

1. `odoo/odoo` — поява гілки самої платформи. Це і є T-0 у машинному вигляді.
   Один `git ls-remote`, без клонування. До T-0 вартовий більше нічого не
   робить: 232 репозиторії кожні 15 хвилин — це трафік без інформації, бо OCA
   не форкає серію раніше за платформу.
2. Docker Hub: чи є тег `odoo:<серія>`. Історія: `19.0` пушнули наступного дня
   після кейноуту, `18.0` — через два тижні після релізу. Тому образ будуємо з
   нічного `.deb`, а цей прапорець лише скасовує потребу в самозбірці.
3. OCA — повний обхід `data/oca_repos.txt`, але тільки ПІСЛЯ появи гілки
   платформи і не частіше разу на годину.

Реакція — рівно три дії, і третьої серед них немає:
  · подія в `eco_events` (унікальна, тому лист буде один, а не потік);
  · зріз у `series_snapshots` з міткою часу виявлення — саме цей рядок згодом і
    буде доказом, що ми були першими;
  · лист власнику.

**Нічого не запускається автоматично.** У перші години 20.0 рухається щогодини,
і автоматичний прогін дасть дані, які застаріють, поки збираються. Прогін
стартує людина.
"""
import os, subprocess, sys, urllib.request, json, time
sys.path.insert(0, os.path.dirname(__file__))
from db import connect, ROOT

SERIES = os.environ.get("WATCH_SERIES", "20.0")
PLATFORM = "https://github.com/odoo/odoo"
OCA_URL = "https://github.com/OCA/{}"
LIST = ROOT / "data" / "oca_repos.txt"
SWEEP_MIN = int(os.environ.get("WATCH_SWEEP_MIN", "60"))
WORKERS = int(os.environ.get("WATCH_WORKERS", "10"))
TAGS_URL = ("https://hub.docker.com/v2/repositories/library/odoo/tags"
            "?page_size=100")


def has_branch(url, series, timeout=60):
    """→ sha гілки або None. Помилка мережі — це None і рядок у лог, не виняток:
    вартовий мусить дожити до наступного тику, а не впасти на першому таймауті.
    """
    try:
        r = subprocess.run(["git", "ls-remote", "--heads", url, series],
                           capture_output=True, text=True, timeout=timeout,
                           env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    except subprocess.TimeoutExpired:
        print(f"  таймаут: {url}", file=sys.stderr)
        return None
    if r.returncode != 0:
        print(f"  ls-remote не вдався ({url}): {r.stderr.strip()[:120]}",
              file=sys.stderr)
        return None
    for line in r.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.strip() == f"refs/heads/{series}":
            return sha[:12]
    return None


def dockerhub_tag(series):
    """Чи є тег у офіційному образі. Публічний JSON, без ключа й без токена."""
    try:
        with urllib.request.urlopen(TAGS_URL, timeout=30) as f:
            data = json.load(f)
    except Exception as e:
        print(f"  Docker Hub недоступний: {e}", file=sys.stderr)
        return None
    return series if any(t.get("name") == series
                         for t in data.get("results", [])) else None


def mark(conn, kind, repo, series, note=""):
    """Подія в eco_events. → True, якщо саме цей запуск її побачив уперше.

    Уся захист від потоку листів тримається на UNIQUE (kind, repo, series):
    вартовий може тикати кожні 15 хвилин роками, лист піде один раз.
    """
    cur = conn.cursor()
    cur.execute("""INSERT INTO eco_events (kind, repo, series) VALUES (%s,%s,%s)
                   ON CONFLICT (kind, repo, series) DO NOTHING RETURNING at""",
                (kind, repo, series))
    row = cur.fetchone()
    if row:
        print(f"  ПОДІЯ: {kind} {repo} {series} {note}".rstrip())
    return bool(row)


def seen(conn, kind, repo, series):
    cur = conn.cursor()
    cur.execute("SELECT at FROM eco_events WHERE kind=%s AND repo=%s AND series=%s",
                (kind, repo, series))
    row = cur.fetchone()
    return row["at"] if row else None


def state_get(conn, key):
    cur = conn.cursor()
    cur.execute("SELECT at FROM watch_state WHERE key=%s", (key,))
    row = cur.fetchone()
    return row["at"] if row else None


def state_set(conn, key, note=""):
    conn.cursor().execute(
        """INSERT INTO watch_state (key, at, note) VALUES (%s, now(), %s)
           ON CONFLICT (key) DO UPDATE SET at = now(), note = EXCLUDED.note""",
        (key, note or None))


def oca_repos():
    if not LIST.exists():
        print(f"  немає {LIST} — обхід OCA пропущено", file=sys.stderr)
        return []
    return [l.strip() for l in LIST.read_text().splitlines() if l.strip()]


def sweep_oca(conn, series):
    """Один повний обхід OCA. → (скільки репозиторіїв мають гілку, нові)."""
    from concurrent.futures import ThreadPoolExecutor
    repos = oca_repos()
    if not repos:
        return 0, []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        shas = list(ex.map(lambda r: (r, has_branch(OCA_URL.format(r), series)),
                           repos))
    with_branch = [r for r, sha in shas if sha]
    fresh = [r for r in with_branch
             if mark(conn, "branch_first", f"OCA/{r}", series)]
    state_set(conn, f"oca_sweep_{series}",
              f"{len(with_branch)} з {len(repos)} мають гілку")
    print(f"  обхід OCA: {len(with_branch)} з {len(repos)} мають {series}, "
          f"нових {len(fresh)}")
    return len(with_branch), fresh


def snapshot(conn, series, repos):
    """Зріз із міткою часу виявлення. Денний унікальний індекс дозволяє один
    рядок на добу — тому оновлюємо його, а не плодимо історію щогодини.
    """
    # Спершу UPDATE, потім INSERT, а не ON CONFLICT: денний унікальний індекс
    # побудований на виразі ((taken_at AT TIME ZONE 'UTC')::date), і виводити
    # по ньому цільовий індекс — саме те місце, де ON CONFLICT тихо не
    # спрацьовує. Явні два кроки коштують нічого й не залежать від виведення.
    cur = conn.cursor()
    cur.execute("""
        UPDATE series_snapshots SET repos = GREATEST(repos, %s)
        WHERE (taken_at AT TIME ZONE 'UTC')::date = (now() AT TIME ZONE 'UTC')::date
          AND series = %s AND method = 'v2'
    """, (repos, series))
    if not cur.rowcount:
        cur.execute("""INSERT INTO series_snapshots (series, repos, modules, method)
                       VALUES (%s,%s,0,'v2')""", (series, repos))


def mail(subject, body):
    """Лист власнику. Немає SMTP — це рядок у журналі, а не падіння вартового:
    подія вже записана в БД, і втратити її через пошту було б безглуздо.

    `except Exception` тут МАЛО, і це не педантизм: `notify.send()` при
    ненаштованому SMTP кидає `SystemExit`, а він не Exception. Перша ж перевірка
    показала рівно це — вартовий помирав відразу після того, як записав подію,
    тобто кроки 2 і 3 у цьому тику не виконувались, а лист про T-0 губився
    назавжди (подія вже позначена, гілка «уперше» більше не спрацює).
    """
    sys.path.insert(0, str(ROOT / "bin"))
    try:
        import notify
        notify.send(subject, body)
        print(f"  лист надіслано: {subject}")
    except (Exception, SystemExit) as e:
        print(f"  лист НЕ надіслано ({e}): {subject}", file=sys.stderr)


def main():
    t0 = time.time()
    conn = connect()
    state_set(conn, f"check_{SERIES}")

    # ── 1. платформа ────────────────────────────────────────────────────────
    sha = has_branch(PLATFORM, SERIES)
    was = seen(conn, "branch_first", "odoo/odoo", SERIES)
    if not sha:
        print(f"вартовий {SERIES}: гілки платформи немає "
              f"({time.time()-t0:.1f}s)")
        conn.close()
        return
    if mark(conn, "branch_first", "odoo/odoo", SERIES, f"sha {sha}"):
        snapshot(conn, SERIES, 0)
        mail(f"T-0: гілка odoo/odoo {SERIES} з'явилася",
             f"git ls-remote показав refs/heads/{SERIES}, sha {sha}.\n"
             f"Виявлено вартовим на сервері, зріз записано в series_snapshots.\n\n"
             f"Автоматично НЕ запущено нічого. Далі, руками:\n"
             f"  1. образ серії (нічний .deb або офіційний тег, коли з'явиться)\n"
             f"  2. bin/mktemplate.sh {SERIES}\n"
             f"  3. harvest + sync_repos + enqueue\n"
             f"  4. один BATCH=1 прогін для перевірки, потім systemd\n")
        was = None

    # ── 2. офіційний образ ─────────────────────────────────────────────────
    if dockerhub_tag(SERIES) and mark(conn, "dockerhub_tag", "library/odoo", SERIES):
        mail(f"Офіційний образ odoo:{SERIES} опубліковано",
             f"Тег odoo:{SERIES} є на Docker Hub. Самозбірка з .deb більше не "
             f"потрібна: достатньо оновити рядок у series_image.\n")

    # ── 3. OCA, не частіше разу на годину ──────────────────────────────────
    last = state_get(conn, f"oca_sweep_{SERIES}")
    due = last is None or (time.time() - last.timestamp()) > SWEEP_MIN * 60
    if due:
        n, fresh = sweep_oca(conn, SERIES)
        if n:
            snapshot(conn, SERIES, n)
    else:
        n = None
        print(f"  обхід OCA пропущено: останній {last:%H:%M UTC}, "
              f"межа {SWEEP_MIN} хв")

    print(f"вартовий {SERIES}: гілка платформи є (sha {sha}, "
          f"{'уперше' if was is None else f'з {was:%d.%m %H:%M UTC}'})"
          + (f", OCA з гілкою: {n}" if n is not None else "")
          + f" · {time.time()-t0:.1f}s")
    conn.close()


if __name__ == "__main__":
    main()
