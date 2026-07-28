import os
import sys
import glob
import shutil
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_URL = os.getenv('SUPABASE_DB_URL') or os.getenv('DATABASE_URL') or f'sqlite:///{os.path.join(BASE_DIR, "volcad.db").replace(os.sep, "/")}'

def restore_sqlite():
    path = DB_URL.replace('sqlite:///', '').lstrip('./')
    path = os.path.abspath(path)
    backups = sorted(glob.glob(os.path.join(BASE_DIR, 'backups', '*_volcad.db')), reverse=True)
    if not backups:
        print('No SQLite backups found in backups/')
        sys.exit(1)
    latest = backups[0]
    print(f'Latest SQLite backup: {latest}')
    print(f'Target database:      {path}')
    ans = input('Overwrite current database with this backup? (y/N): ')
    if ans.lower() != 'y':
        print('Restore cancelled.')
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    shutil.copy2(latest, path)
    print(f'Restored {path} from {latest}')

def main():
    if DB_URL.startswith('sqlite'):
        restore_sqlite()
    else:
        print('Postgres restore is not yet automated. Restore the latest SQL dump with psql:')
        print(f'  psql {DB_URL} < backups/XXXXXX_volcad.sql')
        sys.exit(1)

if __name__ == '__main__':
    main()
