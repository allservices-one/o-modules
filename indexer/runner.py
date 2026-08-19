#!/usr/bin/env python3
"""Воркер прогонів. Запускати рівно 2 копії на 4 vCPU / 8 GB (див. systemd/).

Цикл: взяти задачу з черги (SKIP LOCKED) → створити БД з шаблону → docker run odoo -i
→ класифікувати лог → записати результат → викинути БД.

BATCH>1 вмикає батч-режим із бісекцією: 8 модулів в одну БД, при падінні — розділити.
Для першого масового проходу це дає виграш у 5-8 разів.
"""
import os, re, socket, subprocess, sys, time, uuid
sys.path.insert(0, os.path.dirname(__file__))
from db import connect, ROOT, _password
from classify import classify, tail

WORKER = f"{socket.gethostname()}/{os.getpid()}"
BATCH = int(os.environ.get("BATCH", "1"))
TIMEOUT = int(os.environ.get("RUN_TIMEOUT", "420"))
MEM = os.environ.get("RUN_MEM", "2g")
PGPASS = _password()
# Пароль передаємо в docker ЧЕРЕЗ ОТОЧЕННЯ, а не в argv. Форма `-e ІМʼЯ` без
# значення каже docker узяти змінну з оточення клієнта. Раніше було
# `-e PASSWORD=<пароль>` — і пароль світився в `ps`, `systemctl status`,
# journald та в будь-якому виводі, який хтось скопіює в публічний ops/.
# Репозиторій публічний, тому це не гігієна, а вимога.
CHILD_ENV = dict(os.environ, PASSWORD=PGPASS, PGPASSWORD=PGPASS)
IDLE_SLEEP = int(os.environ.get("IDLE_SLEEP", "30"))
# Скільки задача може висіти в running, перш ніж вважати воркера мертвим.
# Має бути помітно більше за RUN_TIMEOUT, інакше живий батч заберуть із-під нього.
STALE_LOCK_MIN = int(os.environ.get("STALE_LOCK_MIN", "30"))
# MAX_JOBS>0 — обробити стільку задач і вийти. Для ручної перевірки
# (STEPS, «один прогін вручну»); у systemd не задається, там цикл нескінченний.
MAX_JOBS = int(os.environ.get("MAX_JOBS", "0"))


def tmpl(series):
    return "tmpl_" + series.replace(".", "")


def image_for(conn, series):
    """Який образ проганяти для цієї серії.

    Через таблицю, а не через `odoo:{series}` у коді: перехід на похідний образ
    із оголошеними залежностями і перехід 24.09 на офіційний `odoo:20.0` мають
    бути зміною ОДНОГО значення, а не правкою коду. Порожня таблиця означає
    поведінку за замовчуванням, тому нічого не ламається, поки її не заповнили.

    Свідомо БЕЗ кешу: воркери живуть довго з Restart=always, і закешоване
    значення означало б, що кожна зміна образу вимагає перезапуску — тобто
    втрати задач, які саме зараз у роботі. Один дешевий SELECT на батч із
    восьми модулів коштує незрівнянно менше.
    """
    cur = conn.cursor()
    cur.execute("SELECT image FROM series_image WHERE series=%s", (series,))
    row = cur.fetchone()
    return (row or {}).get("image") or f"odoo:{series}"


def psql(sql, db="postgres"):
    return subprocess.run(
        ["docker", "exec", "-i", "-e", "PGPASSWORD", "modidx-pg",
         "psql", "-U", "odoo", "-d", db, "-v", "ON_ERROR_STOP=1",
         "-t", "-A", "-F", "|", "-c", sql],
        capture_output=True, text=True, timeout=120, env=CHILD_ENV)


def run_install(series, modules, dbname, image):
    """Один прогін. → (returncode, log, timed_out, ms)"""
    pool = ROOT / "var" / "pool" / series
    repos = ROOT / "var" / "repos" / series
    t0 = time.time()
    cmd = [
        "docker", "run", "--rm", "--network", "modidx",
        f"--memory={MEM}", "--memory-swap", MEM, "--cpus", "1.5",
        "--pids-limit", "512", "--security-opt", "no-new-privileges",
        "-v", f"{pool}:/mnt/pool:ro",
        # Пул — це симлінки на АБСОЛЮТНІ шляхи хоста (var/repos/<серія>/<репо>/<модуль>).
        # Без цього монтування вони всередині контейнера висять у нікуди, Odoo через
        # _is_addons_path() не бачить у /mnt/pool жодного манифеста, пише
        # "invalid addons directory '/mnt/pool', skipped" — і далі
        # "invalid module names, ignored: <модуль>" при коді виходу 0.
        # Тобто модуль НЕ ставиться, а виглядає як успіх. Перевірено 19.08.2026.
        # Монтуємо тим самим абсолютним шляхом, щоб симлінки резолвились.
        "-v", f"{repos}:{repos}:ro",
        # Параметри БД — тільки через env. Entrypoint образу дописує DB_ARGS з env
        # у кінець команди (exec odoo "$@" "${DB_ARGS[@]}"), тому флаги --db_host
        # і --db_password перебиваються дефолтами 'db' та 'odoo'. Див. mktemplate.sh.
        "-e", "HOST=pg", "-e", "PORT=5432", "-e", "USER=odoo", "-e", "PASSWORD",
        image, "odoo",
        "-d", dbname,
        "--addons-path=/mnt/pool,/usr/lib/python3/dist-packages/odoo/addons",
        "-i", ",".join(modules),
        "--without-demo=all", "--stop-after-init", "--no-http",
        "--max-cron-threads=0", "--log-level=warn", "--limit-time-real", str(TIMEOUT - 30),
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT,
                           env=CHILD_ENV)
        log = (p.stdout or "") + (p.stderr or "")
        return p.returncode, log, False, int((time.time() - t0) * 1000)
    except subprocess.TimeoutExpired as e:
        log = ((e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")) \
            + "__RUNNER_TIMEOUT__"
        return 124, log, True, int((time.time() - t0) * 1000)


SAFE_NAME = re.compile(r"^[A-Za-z0-9_]+$")


def check_installed(dbname, names):
    """Факт установки з робочої БД, ДО її видалення.

    Код виходу — це висновок, `ir_module_module.state='installed'` — факт.
    rc=0 може означати «нічого не робив»: installable=False, неповний
    addons-path, помилка в імені модуля — усі три дають rc=0.

    → {name: (state, latest_version)} або None, якщо перевірити не вдалося
      (БД не піднялася, psql не відповів). None і порожній dict — різні речі:
      None означає «не знаю», і тоді статус з логу НЕ перевизначається.
    """
    safe = [n for n in names if SAFE_NAME.match(n)]
    if not safe:
        return None
    lst = ",".join("'" + n + "'" for n in safe)
    r = psql(f"SELECT name, state, coalesce(latest_version,'') "
             f"FROM ir_module_module WHERE name IN ({lst})", db=dbname)
    if r.returncode != 0:
        return None
    out = {}
    for line in r.stdout.splitlines():
        parts = [c.strip() for c in line.split("|")]
        if len(parts) == 3 and parts[0] in safe:
            out[parts[0]] = (parts[1], parts[2] or None)
    return out


def fresh_db(series):
    name = "job_" + uuid.uuid4().hex[:12]
    r = psql(f'CREATE DATABASE {name} TEMPLATE {tmpl(series)}')
    if r.returncode != 0:
        raise RuntimeError(f"CREATE DATABASE не вдалося: {r.stderr.strip()[:300]}")
    return name


def drop_db(name):
    psql(f'DROP DATABASE IF EXISTS {name} WITH (FORCE)')


def record(conn, module_id, series, head_sha, status, cause, detail, log, ms,
           batched, latest_version=None, image=None):
    conn.cursor().execute("""
        INSERT INTO runs (module_id, series, head_sha, status, cause, detail,
                          log_tail, duration_ms, odoo_image, batched, latest_version)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (module_id, series, head_sha, status, cause, detail,
          tail(log) if status not in ("ok",) else None, ms,
          image or f"odoo:{series}", batched, latest_version))


def reclaim(conn):
    """Повернути в чергу задачі мертвих воркерів.

    Без цього вбитий воркер лишає свій батч у running НАЗАВЖДИ: claim() бере
    лише queued, а нічого не знімає лок. Спіймано 19.08.2026 на замірі BATCH=16,
    обірваному по таймауту, — 8 задач зависли й повернути їх довелося руками.
    Під systemd це не крайній випадок, а норма: рестарт, OOM, деплой.

    Довіряти locked_at можна: його ставить той самий UPDATE, що й state.
    """
    cur = conn.cursor()
    cur.execute("""
        UPDATE jobs SET state='queued', locked_by=NULL, locked_at=NULL
        WHERE state='running' AND locked_at < now() - (%s || ' minutes')::interval
        RETURNING id
    """, (STALE_LOCK_MIN,))
    n = len(cur.fetchall())
    if n:
        print(f"  ↺ повернуто в чергу завислих задач: {n}"
              f" (лок старший за {STALE_LOCK_MIN} хв)", flush=True)
    return n


def claim(conn, limit):
    """Взяти задачі з черги. FOR UPDATE SKIP LOCKED — тому 2 воркери не б'ються.

    Однорідність батчу за серією забезпечує САМ ЗАПИТ, а не відсів у Python.
    Раніше `UPDATE state='running'` бив по всіх відібраних рядках, а поверталися
    лише ті, що збіглися за серією з першим, — решта лишалася `running` назавжди.
    На BATCH=1 не проявлялося, на межі серій тихо губило задачі.

    `FOR UPDATE OF j` обов'язковий: у запиті з join до `head` звичайний
    `FOR UPDATE` спробує залокати і `head`, а це агрегат — Postgres відмовить.

    Гонка тут безпечна: якщо два воркери обчислять ту саму головну серію — це
    саме те, що потрібно, а SKIP LOCKED розведе їх по різних рядках.
    """
    cur = conn.cursor()
    cur.execute("""
        WITH head AS (
          SELECT series FROM jobs WHERE state='queued' ORDER BY priority, id LIMIT 1
        ), pick AS (
          SELECT j.id FROM jobs j, head
          WHERE j.state='queued' AND j.series = head.series
          ORDER BY j.priority, j.id
          LIMIT %s
          FOR UPDATE OF j SKIP LOCKED
        )
        UPDATE jobs j SET state='running', locked_by=%s, locked_at=now(), attempts=attempts+1
        FROM pick WHERE j.id = pick.id
        RETURNING j.id, j.module_id, j.series
    """, (limit, WORKER))
    jobs = cur.fetchall()
    if not jobs:
        return []
    ids = tuple(j["module_id"] for j in jobs)
    cur.execute("SELECT id, repo, module, series, head_sha FROM modules WHERE id IN %s", (ids,))
    meta = {m["id"]: m for m in cur.fetchall()}
    return [(j, meta[j["module_id"]]) for j in jobs]


def finish(conn, job_ids):
    """Успішно оброблену задачу ВИДАЛЯЄМО з черги.

    Історія прогонів живе в `runs`; черзі вона не потрібна. Раніше тут стояв
    UPDATE state='done', і на другому проході harvest (новий head_sha → друга
    задача на той самий модуль) її фінальний UPDATE зіткнувся б із рядком 'done'
    від першого проходу через UNIQUE (module_id, state). Констрейнт прибрано,
    натомість частковий jobs_active_uniq лише на queued/running — див. schema.sql.
    """
    if job_ids:
        conn.cursor().execute("DELETE FROM jobs WHERE id IN %s", (tuple(job_ids),))


def fail_jobs(conn, job_ids):
    """Задачу, що впала в самому харнесі, лишаємо в черзі зі станом error —
    щоб було видно, що падало. Частковий індекс її не покриває, дублів не буде."""
    if job_ids:
        conn.cursor().execute(
            "UPDATE jobs SET state='error' WHERE id IN %s", (tuple(job_ids),))


MARK = {"ok": "✓", "warn": "!", "dep": "▲", "env": "~", "fail": "✗", "timeout": "⏱"}


def process(conn, items):
    """items: [(job, module)] однієї серії. Батч із бісекцією."""
    series = items[0][1]["series"]
    names = [m["module"] for _, m in items]
    image = image_for(conn, series)
    db = fresh_db(series)
    try:
        rc, log, to, ms = run_install(series, names, db, image)
        # ДО drop_db: сама БД і є доказом. Після видалення питати нема в кого.
        inst = check_installed(db, names) if not to else None
    finally:
        drop_db(db)

    if rc == 0 or len(items) == 1:
        base = classify(log, rc, to)
        per = ms // max(1, len(items))
        marks = []
        for _, m in items:
            status, cause, detail = base
            ver = None
            if inst is not None:
                st, ver = inst.get(m["module"], (None, None))
                if rc == 0 and st != "installed":
                    # Весь клас «тихого успіху» одним місцем: installable=False,
                    # неповний addons-path, помилка в імені. Це збій харнесу або
                    # властивість пакування, а НЕ несумісність із версією, тому env.
                    status, cause = "env", "not_installed_despite_rc0"
                    detail = ("rc=0, але модуль не встановлено: ir_module_module.state="
                              + (st or "запису немає"))
            record(conn, m["id"], series, m["head_sha"], status, cause, detail, log,
                   per, len(items) > 1, ver, image)
            marks.append(MARK.get(status, "?"))
        finish(conn, [j["id"] for j, _ in items])
        # У батчі статуси тепер можуть різнитися по модулях — друкуємо по одному
        # знаку на модуль. Це і є відповідь «які саме не стали» без бісекції.
        print(f"  {''.join(marks)} [{series}] {', '.join(names)[:70]}"
              f" {base[0]}/{base[1] or '-'} {ms}ms", flush=True)
        return

    # Батч упав — ділимо навпіл, щоб знайти винуватця.
    # Причину друкуємо ТУТ: у runs цей батч не потрапляє (записуються лише
    # половини), тому без цього рядка єдиний слід того, чому впав великий батч,
    # зникає безслідно. Саме так 19.08.2026 лишилося невідомим, чому впав BATCH=16.
    st, cs, det = classify(log, rc, to)
    print(f"  ↯ батч із {len(items)} упав (rc={rc}, {st}/{cs or '-'}): "
          f"{(det or '')[:160]} — бісекція", flush=True)
    mid = len(items) // 2
    process(conn, items[:mid])
    process(conn, items[mid:])


def main():
    conn = connect()
    print(f"воркер {WORKER} · BATCH={BATCH} · MEM={MEM}"
          + (f" · MAX_JOBS={MAX_JOBS}" if MAX_JOBS else ""), flush=True)
    reclaim(conn)          # підібрати за мертвими воркерами до першого claim
    idle = 0
    done = 0
    last_reclaim = time.time()
    while True:
        if time.time() - last_reclaim > 600:
            reclaim(conn)
            last_reclaim = time.time()
        items = claim(conn, BATCH)
        if not items:
            if MAX_JOBS:            # ручний прогін: черга порожня — виходимо, а не чекаємо
                print("  черга порожня, вихід", flush=True)
                return
            idle += 1
            if idle == 1:
                print("  черга порожня, чекаю", flush=True)
            time.sleep(IDLE_SLEEP)
            continue
        idle = 0
        try:
            process(conn, items)
        except Exception as e:
            print(f"  ! помилка обробки: {e}", flush=True)
            fail_jobs(conn, [j["id"] for j, _ in items])
        done += len(items)
        if MAX_JOBS and done >= MAX_JOBS:
            print(f"  MAX_JOBS={MAX_JOBS} досягнуто, вихід", flush=True)
            return


if __name__ == "__main__":
    main()
