import os
import sys
import json
import hashlib
import subprocess
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

def get_db_url():
    db_url = os.getenv('SUPABASE_DB_URL')
    if not db_url:
        print('Error: SUPABASE_DB_URL not set in environment or .env')
        sys.exit(1)
    return db_url.replace('postgresql+psycopg2://', 'postgresql://')

def run_schema():
    print('Ensuring schema is applied...')
    result = subprocess.run([sys.executable, 'setup_db.py'], capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        sys.exit(1)

def load_customer(path):
    if not os.path.exists(path):
        print(f'Error: customer config file not found: {path}')
        sys.exit(1)
    with open(path, 'r') as f:
        return json.load(f)

def insert_config(cur, agency_id, category, key, value):
    cur.execute('''
        INSERT INTO customer_config (agency_id, category, key, value)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (agency_id, category, key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
    ''', (agency_id, category, key, json.dumps(value)))

def hash_password(password):
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def main():
    customer_path = sys.argv[1] if len(sys.argv) > 1 else 'customer.json'
    data = load_customer(customer_path)
    run_schema()
    db_url = get_db_url()
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    try:
        # Global configuration (agency_id = NULL)
        config = data.get('config', {})
        for cat, items in config.items():
            if isinstance(items, dict):
                for key, value in items.items():
                    insert_config(cur, None, cat, key, value)
            elif isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        key = item.get('name') or item.get('key')
                        value = item
                    else:
                        key = item
                        value = {'name': item}
                    insert_config(cur, None, cat, key, value)

        # Insert agencies and keep id map
        agency_map = {}
        agencies = data.get('agencies', [])
        for agency in agencies:
            cur.execute('''
                INSERT INTO agencies (name, agency_type, domain, address, city, state, zip_code, lat, lng, approved, approved_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, NOW())
                ON CONFLICT (domain) DO UPDATE SET name = EXCLUDED.name RETURNING id
            ''', (agency.get('name'), agency.get('agency_type', 'fire'), agency.get('domain'), agency.get('address'), agency.get('city'), agency.get('state'), agency.get('zip_code'), agency.get('lat'), agency.get('lng')))
            agency_id = cur.fetchone()[0]
            agency_map[agency.get('name')] = agency_id
            # agency-specific config
            a_config = agency.get('config', {})
            for cat, items in a_config.items():
                if isinstance(items, dict):
                    for key, value in items.items():
                        insert_config(cur, agency_id, cat, key, value)
                elif isinstance(items, list):
                    for item in items:
                        key = item if isinstance(item, str) else item.get('name') or item.get('key')
                        value = item if not isinstance(item, str) else {'name': item}
                        insert_config(cur, agency_id, cat, key, value)

        # Insert users and keep id map
        user_map = {}
        users = data.get('users', [])
        for user in users:
            agency_id = agency_map.get(user.get('agency')) if user.get('agency') else None
            pwd = user.get('password') or 'changeme'
            cur.execute('''
                INSERT INTO users (email, hashed_password, role, is_active, agency_id)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (email) DO UPDATE SET role = EXCLUDED.role, agency_id = EXCLUDED.agency_id, is_active = EXCLUDED.is_active RETURNING id
            ''', (user.get('email'), hash_password(pwd), user.get('role', 'responder'), user.get('is_active', True), agency_id))
            uid = cur.fetchone()[0]
            user_map[user.get('email')] = uid

        # Insert units and keep id map
        unit_map = {}
        units = data.get('units', [])
        for unit in units:
            agency_id = agency_map.get(unit.get('agency'))
            if not agency_id:
                print(f'Skipping unit {unit.get("call_sign")}: agency not found')
                continue
            cur.execute('''
                INSERT INTO units (agency_id, name, call_sign, unit_type, lat, lng, camera_url)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (call_sign) DO UPDATE SET agency_id = EXCLUDED.agency_id, name = EXCLUDED.name, unit_type = EXCLUDED.unit_type, lat = EXCLUDED.lat, lng = EXCLUDED.lng, camera_url = EXCLUDED.camera_url RETURNING id
            ''', (agency_id, unit.get('name'), unit.get('call_sign'), unit.get('unit_type'), unit.get('lat'), unit.get('lng'), unit.get('camera_url')))
            uid = cur.fetchone()[0]
            unit_map[unit.get('call_sign')] = uid

        # Insert personnel
        personnel = data.get('personnel', [])
        for p in personnel:
            agency_id = agency_map.get(p.get('agency'))
            user_id = user_map.get(p.get('email'))
            unit_id = unit_map.get(p.get('unit'))
            if not agency_id:
                print(f'Skipping personnel {p.get("first_name")}: agency not found')
                continue
            cur.execute('''
                INSERT INTO personnel (user_id, agency_id, first_name, last_name, radio_id, phone, email, sms_phone, duty_status, current_unit_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            ''', (user_id, agency_id, p.get('first_name'), p.get('last_name'), p.get('radio_id'), p.get('phone'), p.get('email'), p.get('sms_phone'), p.get('duty_status', 'off_duty'), unit_id))

        conn.commit()
        print('Customer setup complete.')
        print(f'Agencies: {len(agencies)}, Users: {len(users)}, Units: {len(units)}, Personnel: {len(personnel)}')
    except Exception as e:
        conn.rollback()
        print(f'Error during setup: {e}')
        sys.exit(1)
    finally:
        cur.close()
        conn.close()

if __name__ == '__main__':
    main()
