import argparse
import os
import re
import subprocess
import sys
import sqlparse
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv('SUPABASE_DB_URL') or os.getenv('DATABASE_URL')
if not DB_URL:
    print('Error: No database URL configured. Set SUPABASE_DB_URL or DATABASE_URL in .env')
    sys.exit(1)

# psycopg2 expects postgresql://, not postgresql+psycopg2://
DB_URL = DB_URL.replace('postgresql+psycopg2://', 'postgresql://')

parser = argparse.ArgumentParser(description='Apply schema.sql safely without losing data.')
parser.add_argument('--no-backup', action='store_true', help='Skip the pre-update backup.')
parser.add_argument('--force', action='store_true', help='Allow destructive statements (DROP/TRUNCATE/DELETE).')
args = parser.parse_args()


def is_destructive(stmt):
    s = stmt.lower()
    # Block data-destructive keywords. CREATE/DROP INDEX is safe, but DROP TABLE/COLUMN/TRUNCATE/DELETE is not.
    if re.search(r'\btruncate\b', s):
        return True
    if re.search(r'\bdelete\s+from\b', s):
        return True
    # DROP is destructive unless it is only dropping an index.
    if re.search(r'\bdrop\b', s) and not re.search(r'\bdrop\s+(index|tablespace)\b', s):
        return True
    return False


if not args.no_backup:
    print('Creating pre-update backup...')
    subprocess.run([sys.executable, 'backup.py'], check=True)

with open('schema.sql', 'r') as f:
    sql = f.read()

statements = [s for s in sqlparse.split(sql) if s.strip()]

blocked = False
for stmt in statements:
    if is_destructive(stmt) and not args.force:
        print(f'Blocked destructive statement (use --force to override): {stmt[:120]}')
        blocked = True
        continue

if blocked and not args.force:
    print('Schema setup aborted because destructive statements were found. No changes applied.')
    sys.exit(1)

if DB_URL.startswith('sqlite'):
    print('Detected SQLite; using app.py schema management.')
    import app
    app.init_sqlite_db()
    print('SQLite schema setup complete.')
    sys.exit(0)

import psycopg2
conn = psycopg2.connect(DB_URL)
cur = conn.cursor()
for stmt in statements:
    if is_destructive(stmt) and not args.force:
        continue
    try:
        cur.execute(stmt)
        print(f'Executed: {stmt.split()[1] if len(stmt.split()) > 1 else stmt.split()[0]}')
    except psycopg2.Error as e:
        print(f'Error: {e}')
        conn.rollback()
    else:
        conn.commit()
cur.close()
conn.close()
print('Schema setup complete.')
