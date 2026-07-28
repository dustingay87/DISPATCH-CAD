import argparse
import os
import sys
import json
import hashlib
import subprocess
from datetime import datetime
from dotenv import load_dotenv
import app

load_dotenv()

def run_schema():
    print('Ensuring schema is applied...')
    # setup_db.py will not backup here; the caller is responsible for backup.
    result = subprocess.run([sys.executable, 'setup_db.py', '--no-backup'], capture_output=True, text=True)
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

def hash_password(password):
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def insert_config(session, agency_id, category, key, value):
    cfg = session.query(app.CustomerConfig).filter_by(agency_id=agency_id, category=category, key=key).first()
    if cfg:
        cfg.value = value
    else:
        session.add(app.CustomerConfig(agency_id=agency_id, category=category, key=key, value=value))

def main():
    parser = argparse.ArgumentParser(description='Seed or update a customer configuration without destroying existing data.')
    parser.add_argument('customer', nargs='?', default='customer.json', help='Path to customer JSON config')
    parser.add_argument('--no-backup', action='store_true', help='Skip the pre-update database backup (not recommended).')
    args = parser.parse_args()

    customer_path = args.customer
    data = load_customer(customer_path)

    if not args.no_backup:
        print('Creating pre-update backup...')
        subprocess.run([sys.executable, 'backup.py'], check=True)

    run_schema()
    session = app.SessionLocal()

    try:
        # Global configuration (agency_id = NULL)
        config = data.get('config', {})
        for cat, items in config.items():
            if isinstance(items, dict):
                for key, value in items.items():
                    insert_config(session, None, cat, key, value)
            elif isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        key = item.get('name') or item.get('key')
                        value = item
                    else:
                        key = item
                        value = {'name': item}
                    insert_config(session, None, cat, key, value)

        # Insert agencies and keep id map
        agency_map = {}
        agencies = data.get('agencies', [])
        for agency in agencies:
            a = session.query(app.Agency).filter(app.Agency.domain == agency.get('domain')).first()
            if not a:
                a = app.Agency()
                session.add(a)
            a.name = agency.get('name')
            a.agency_type = agency.get('agency_type', 'fire')
            a.domain = agency.get('domain')
            a.address = agency.get('address')
            a.city = agency.get('city')
            a.state = agency.get('state')
            a.zip_code = agency.get('zip_code')
            a.lat = agency.get('lat')
            a.lng = agency.get('lng')
            a.approved = True
            a.approved_at = datetime.utcnow()
            session.flush()
            agency_map[agency.get('name')] = a.id

            a_config = agency.get('config', {})
            for cat, items in a_config.items():
                if isinstance(items, dict):
                    for key, value in items.items():
                        insert_config(session, a.id, cat, key, value)
                elif isinstance(items, list):
                    for item in items:
                        key = item if isinstance(item, str) else item.get('name') or item.get('key')
                        value = item if not isinstance(item, str) else {'name': item}
                        insert_config(session, a.id, cat, key, value)

        # Insert users and keep id map
        user_map = {}
        users = data.get('users', [])
        for user in users:
            agency_id = agency_map.get(user.get('agency')) if user.get('agency') else None
            pwd = user.get('password') or 'changeme'
            u = session.query(app.User).filter(app.User.email == user.get('email')).first()
            if not u:
                u = app.User(email=user.get('email'))
                session.add(u)
            u.hashed_password = hash_password(pwd)
            u.role = user.get('role', 'responder')
            u.is_active = user.get('is_active', True)
            u.agency_id = agency_id
            session.flush()
            user_map[user.get('email')] = u.id

        # Insert units and keep id map
        unit_map = {}
        units = data.get('units', [])
        for unit in units:
            agency_id = agency_map.get(unit.get('agency'))
            if not agency_id:
                print(f'Skipping unit {unit.get("call_sign")}: agency not found')
                continue
            u = session.query(app.Unit).filter(app.Unit.call_sign == unit.get('call_sign')).first()
            if not u:
                u = app.Unit(call_sign=unit.get('call_sign'))
                session.add(u)
            u.agency_id = agency_id
            u.name = unit.get('name')
            u.unit_type = unit.get('unit_type')
            u.lat = unit.get('lat')
            u.lng = unit.get('lng')
            u.camera_url = unit.get('camera_url')
            session.flush()
            unit_map[unit.get('call_sign')] = u.id

        # Insert personnel
        personnel = data.get('personnel', [])
        for p in personnel:
            agency_id = agency_map.get(p.get('agency'))
            user_id = user_map.get(p.get('email'))
            unit_id = unit_map.get(p.get('unit'))
            if not agency_id:
                print(f'Skipping personnel {p.get("first_name")}: agency not found')
                continue
            q = session.query(app.Personnel).filter_by(agency_id=agency_id)
            if p.get('email'):
                q = q.filter(app.Personnel.email == p.get('email'))
            else:
                q = q.filter(app.Personnel.first_name == p.get('first_name'), app.Personnel.last_name == p.get('last_name'))
            pe = q.first()
            if not pe:
                pe = app.Personnel(agency_id=agency_id)
                session.add(pe)
            pe.user_id = user_id
            pe.first_name = p.get('first_name')
            pe.last_name = p.get('last_name')
            pe.radio_id = p.get('radio_id')
            pe.phone = p.get('phone')
            pe.email = p.get('email')
            pe.sms_phone = p.get('sms_phone')
            pe.duty_status = p.get('duty_status', 'off_duty')
            pe.current_unit_id = unit_id

        session.commit()
        print('Customer setup complete.')
        print(f'Agencies: {len(agencies)}, Users: {len(users)}, Units: {len(units)}, Personnel: {len(personnel)}')
    except Exception as e:
        session.rollback()
        print(f'Error during setup: {e}')
        sys.exit(1)
    finally:
        session.close()

if __name__ == '__main__':
    main()
