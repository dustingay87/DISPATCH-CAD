import os
import sys
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text

load_dotenv()

DB_URL = os.getenv('SUPABASE_DB_URL') or os.getenv('DATABASE_URL')
if not DB_URL:
    print('Error: No database URL configured. Set DATABASE_URL or SUPABASE_DB_URL in .env')
    sys.exit(1)

# psycopg2 expects postgresql://, not postgresql+psycopg2://
DB_URL = DB_URL.replace('postgresql+psycopg2://', 'postgresql://')
engine = create_engine(DB_URL)
is_sqlite = DB_URL.startswith('sqlite')

COLUMNS = {
    'departed_scene_at': 'TIMESTAMP' if not is_sqlite else 'TEXT',
    'arrived_destination_at': 'TIMESTAMP' if not is_sqlite else 'TEXT',
    'pickup_address': 'TEXT',
    'dropoff_address': 'TEXT',
    'passenger_count': 'INTEGER'
}


def migrate():
    with engine.connect() as conn:
        inspector = inspect(engine)
        if 'transport_legs' not in inspector.get_table_names():
            print('transport_legs table does not exist yet; run the app to create it.')
            return
        existing_cols = {c['name'] for c in inspector.get_columns('transport_legs')}
        for col_name, col_type in COLUMNS.items():
            if col_name in existing_cols:
                print(f'Column transport_legs.{col_name} already exists')
                continue
            try:
                conn.execute(text(f'ALTER TABLE "transport_legs" ADD COLUMN "{col_name}" {col_type}'))
                conn.commit()
                print(f'Added column transport_legs.{col_name}')
            except Exception as e:
                print(f'Warning: transport_legs.{col_name}: {e}')
    print('Migration complete.')


if __name__ == '__main__':
    migrate()
