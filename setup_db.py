import os
import sys
import sqlparse
import psycopg2
from dotenv import load_dotenv

load_dotenv()

db_url = os.getenv('SUPABASE_DB_URL')
if not db_url:
    print('Error: SUPABASE_DB_URL not set in .env')
    sys.exit(1)

# psycopg2 expects postgresql://, not postgresql+psycopg2://
db_url = db_url.replace('postgresql+psycopg2://', 'postgresql://')

with open('schema.sql', 'r') as f:
    sql = f.read()

statements = [s for s in sqlparse.split(sql) if s.strip()]

conn = psycopg2.connect(db_url)
cur = conn.cursor()
for stmt in statements:
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
