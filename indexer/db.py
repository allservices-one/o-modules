import os, pathlib
import psycopg2, psycopg2.extras

ROOT = pathlib.Path(os.environ.get("ROOT", "/srv/modidx"))
SERIES = os.environ.get("SERIES", "17.0 18.0 19.0").split()

def _password():
    if os.environ.get("PGPASSWORD"):
        return os.environ["PGPASSWORD"]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("PGPASSWORD="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("PGPASSWORD не знайдено (ні в env, ні в .env)")

def connect(dbname="modidx"):
    c = psycopg2.connect(
        host=os.environ.get("PGHOST", "127.0.0.1"),
        port=int(os.environ.get("PGPORT", "5432")),
        user="odoo", password=_password(), dbname=dbname,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    c.autocommit = True
    return c
