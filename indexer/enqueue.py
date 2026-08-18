#!/usr/bin/env python3
"""Ставить у чергу прогони: усе, що ще не тестувалося або чий head_sha змінився.

Пріоритети: спершу те, що люди дивитимуться найчастіше.
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from db import connect, SERIES

def main():
    conn = connect(); cur = conn.cursor()
    total = 0
    for s in SERIES:
        cur.execute("""
        WITH need AS (
          SELECT m.id, m.series,
                 CASE
                   WHEN m.series = (SELECT max(series) FROM modules) THEN 10  -- найновіша серія найважливіша
                   ELSE 100
                 END AS prio
          FROM modules m
          LEFT JOIN latest_runs r ON r.module_id = m.id
          WHERE m.series = %s
            AND (r.id IS NULL OR r.head_sha IS DISTINCT FROM m.head_sha)
            AND NOT EXISTS (
              SELECT 1 FROM jobs j
              WHERE j.module_id = m.id AND j.state IN ('queued','running')
            )
        )
        INSERT INTO jobs (module_id, series, priority)
        SELECT id, series, prio FROM need
        """, (s,))
        print(f"  {s}: у чергу додано {cur.rowcount}")
        total += cur.rowcount
    cur.execute("SELECT state, count(*) c FROM jobs GROUP BY state ORDER BY state")
    print("\nчерга:", {r["state"]: r["c"] for r in cur.fetchall()})
    conn.close()
    print(f"всього додано: {total}")

if __name__ == "__main__":
    main()
