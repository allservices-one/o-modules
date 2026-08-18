#!/usr/bin/env python3
"""Воркер прогонів. Запускати рівно 2 копії на 4 vCPU / 8 GB (див. systemd/).

Цикл: взяти задачу з черги (SKIP LOCKED) → створити БД з шаблону → docker run odoo -i
→ класифікувати лог → записати результат → викинути БД.

BATCH>1 вмикає батч-режим із бісекцією: 8 модулів в одну БД, при падінні — розділити.
Для першого масового проходу це дає виграш у 5-8 разів.
"""
import os, socket, subprocess, sys, time, uuid
sys.path.insert(0, os.path.dirname(__file__))
from db import connect, ROOT, _password
from classify import classify, tail

WORKER = f"{socket.gethostname()}/{os.getpid()}"
BATCH = int(os.environ.get("BATCH", "1"))
TIMEOUT = int(os.environ.get("RUN_TIMEOUT", "420"))
MEM = os.environ.get("RUN_MEM", "2g")
PGPASS = _password()
IDLE_SLEEP = int(os.environ.get("IDLE_SLEEP", "30"))


def tmpl(series):
    return "tmpl_" + series.replace(".", "")


def psql(sql, db="postgres"):
    return subprocess.run(
        ["docker", "exec", "-i", "-e", f"PGPASSWORD={PGPASS}", "modidx-pg",
         "psql", "-U", "odoo", "-d", db, "-v", "ON_ERROR_STOP=1", "-c", sql],
        capture_output=True, text=True, timeout=120)


def run_install(series, modules, dbname):
    """Один прогін. → (returncode, log, timed_out, ms)"""
    pool = ROOT / "var" / "pool" / series
    t0 = time.time()
    cmd = [
        "docker", "run", "--rm", "--network", "modidx",
        f"--memory={MEM}", "--memory-swap", MEM, "--cpus", "1.5",
        "--pids-limit", "512", "--security-opt", "no-new-privileges",
        "-v", f"{pool}:/mnt/pool:ro",
        f"odoo:{series}", "odoo",
        "-d", dbname, "--db_host=pg", "--db_user=odoo", f"--db_password={PGPASS}",
        "--addons-path=/mnt/pool,/usr/lib/python3/dist-packages/odoo/addons",
        "-i", ",".join(modules),
        "--without-demo=all", "--stop-after-init", "--no-http",
        "--max-cron-threads=0", "--log-level=warn", "--limit-time-real", str(TIMEOUT - 30),
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
        log = (p.stdout or "") + (p.stderr or "")
        return p.returncode, log, False, int((time.time() - t0) * 1000)
    except subprocess.TimeoutExpired as e:
        log = ((e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")) \
            + "__RUNNER_TIMEOUT__"
        return 124, log, True, int((time.time() - t0) * 1000)


def fresh_db(series):
    name = "job_" + uuid.uuid4().hex[:12]
    r = psql(f'CREATE DATABASE {name} TEMPLATE {tmpl(series)}')
    if r.returncode != 0:
        raise RuntimeError(f"CREATE DATABASE не вдалося: {r.stderr.strip()[:300]}")
    return name


def drop_db(name):
    psql(f'DROP DATABASE IF EXISTS {name} WITH (FORCE)')


def record(conn, module_id, series, head_sha, status, cause, detail, log, ms, batched):
    conn.cursor().execute("""
        INSERT INTO runs (module_id, series, head_sha, status, cause, detail,
                          log_tail, duration_ms, odoo_image, batched)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (module_id, series, head_sha, status, cause, detail,
          tail(log) if status not in ("ok",) else None, ms, f"odoo:{series}", batched))


def claim(conn, limit):
    """Взяти задачі з черги. FOR UPDATE SKIP LOCKED — тому 2 воркери не б'ються."""
    cur = conn.cursor()
    cur.execute("""
        WITH pick AS (
          SELECT j.id FROM jobs j
          WHERE j.state = 'queued'
          ORDER BY j.priority, j.id
          LIMIT %s
          FOR UPDATE SKIP LOCKED
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
    # батч має бути однорідним за серією
    series = jobs[0]["series"]
    return [(j, meta[j["module_id"]]) for j in jobs if j["series"] == series]


def finish(conn, job_ids, state="done"):
    if job_ids:
        conn.cursor().execute("UPDATE jobs SET state=%s WHERE id IN %s", (state, tuple(job_ids)))


def process(conn, items):
    """items: [(job, module)] однієї серії. Батч із бісекцією."""
    series = items[0][1]["series"]
    names = [m["module"] for _, m in items]
    db = fresh_db(series)
    try:
        rc, log, to, ms = run_install(series, names, db)
    finally:
        drop_db(db)

    if rc == 0 or len(items) == 1:
        status, cause, detail = classify(log, rc, to)
        for _, m in items:
            record(conn, m["id"], series, m["head_sha"], status, cause, detail, log,
                   ms // max(1, len(items)), len(items) > 1)
        finish(conn, [j["id"] for j, _ in items])
        mark = {"ok": "✓", "warn": "!", "dep": "▲", "env": "~", "fail": "✗", "timeout": "⏱"}.get(status, "?")
        print(f"  {mark} [{series}] {', '.join(names)[:70]} {status}/{cause or '-'} {ms}ms", flush=True)
        return

    # батч упав — ділимо навпіл, щоб знайти винуватця
    print(f"  ↯ батч із {len(items)} упав, бісекція", flush=True)
    mid = len(items) // 2
    process(conn, items[:mid])
    process(conn, items[mid:])


def main():
    conn = connect()
    print(f"воркер {WORKER} · BATCH={BATCH} · MEM={MEM}", flush=True)
    idle = 0
    while True:
        items = claim(conn, BATCH)
        if not items:
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
            finish(conn, [j["id"] for j, _ in items], "error")


if __name__ == "__main__":
    main()
