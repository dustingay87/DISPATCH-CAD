import os
import sys
import subprocess
import shutil
import datetime
import json
import sqlite3
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_URL = os.getenv('SUPABASE_DB_URL') or os.getenv('DATABASE_URL')
if not DB_URL:
    DB_URL = f'sqlite:///{os.path.join(BASE_DIR, "volcad.db").replace(os.sep, "/")}'
BACKUP_DIR = 'backups'


def _timestamp():
    return datetime.datetime.now().strftime('%Y%m%d_%H%M%S')


def _pg_conn_dict(url):
    p = urlparse(url)
    return {
        'host': p.hostname,
        'port': p.port or 5432,
        'dbname': p.path.lstrip('/'),
        'user': p.username,
        'password': p.password,
    }


def backup_postgres(url):
    """Backup a PostgreSQL database. Prefer pg_dump, fall back to JSON table dump."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = _timestamp()
    out_sql = os.path.join(BACKUP_DIR, f'{ts}_volcad.sql')

    try:
        c = _pg_conn_dict(url)
        conninfo = f"host={c['host']} port={c['port']} dbname={c['dbname']} user={c['user']} password={c['password']} sslmode=require"
        cmd = ['pg_dump', '--clean', '--if-exists', '--no-owner', conninfo]
        with open(out_sql, 'w', encoding='utf-8') as f:
            subprocess.run(cmd, stdout=f, check=True, text=True)
        print(f'Backup written to {out_sql}')
        return out_sql
    except FileNotFoundError:
        print('pg_dump not found; falling back to JSON table dump.')
    except subprocess.CalledProcessError as e:
        print(f'pg_dump failed: {e}; falling back to JSON table dump.')

    return _fallback_postgres_backup(url, ts)


def _fallback_postgres_backup(url, ts):
    try:
        import psycopg2
    except ImportError:
        print('psycopg2 is not installed; cannot create a PostgreSQL backup.', file=sys.stderr)
        sys.exit(1)

    c = _pg_conn_dict(url)
    conn = psycopg2.connect(
        host=c['host'], port=c['port'], dbname=c['dbname'],
        user=c['user'], password=c['password'], sslmode='require'
    )
    cur = conn.cursor()

    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name;
    """)
    tables = [r[0] for r in cur.fetchall()]
    data = {}
    for table in tables:
        cur.execute(f'SELECT * FROM "{table}"')
        cols = [d[0] for d in cur.description]
        data[table] = [dict(zip(cols, row)) for row in cur.fetchall()]

    cur.close()
    conn.close()

    os.makedirs(BACKUP_DIR, exist_ok=True)
    out_json = os.path.join(BACKUP_DIR, f'{ts}_volcad.json')
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump(data, f, default=str, indent=2)
    print(f'JSON backup written to {out_json}')
    return out_json


def backup_sqlite(url):
    path = url.replace('sqlite:///', '')
    if path.startswith('./'):
        path = path[2:]
    path = os.path.abspath(path)
    if not os.path.exists(path):
        print(f'SQLite database not found: {path}', file=sys.stderr)
        sys.exit(1)

    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = _timestamp()
    out = os.path.join(BACKUP_DIR, f'{ts}_volcad.db')
    shutil.copy2(path, out)
    print(f'Backup written to {out}')
    return out


def main():
    if DB_URL.startswith('sqlite'):
        backup_sqlite(DB_URL)
    else:
        backup_postgres(DB_URL)


if __name__ == '__main__':
    main()
