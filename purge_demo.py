#!/usr/bin/env python3
"""Purge demo/pilot agency data and optionally the customer/user tied to it."""
import argparse
import os
import re
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

DEFAULT_DB = 'volcad_test.db'
DEMO_DOMAINS = ('pilot.police', 'pilot.fire', 'pilot.ems')
DEMO_NAMES = ('City Police', 'Metro Fire', 'County EMS', 'Demo Fire')


def get_tables(conn):
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    return [r[0] for r in cur.fetchall()]


def get_columns(conn, table):
    return {r[1]: r for r in conn.execute(f'PRAGMA table_info("{table}")')}


def backup_db(path):
    ts = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = f'{path}.backup-{ts}.db'
    shutil.copy2(path, backup)
    return backup


def find_demo_agencies(conn):
    cols = get_columns(conn, 'agencies')
    has_customer = 'customer_id' in cols
    select = 'id, customer_id, name, domain' if has_customer else 'id, name, domain'
    placeholders = ','.join('?' * len(DEMO_DOMAINS))
    q = f"""SELECT {select} FROM agencies
            WHERE domain IN ({placeholders}) OR name IN ({','.join('?'*len(DEMO_NAMES))})"""
    rows = conn.execute(q, (*DEMO_DOMAINS, *DEMO_NAMES)).fetchall()
    if has_customer:
        return rows
    return [(r[0], None, r[1], r[2]) for r in rows]


def plan_purge(conn, include_customer=False, include_users=False):
    agencies = find_demo_agencies(conn)
    if not agencies:
        return None, 'No demo agencies found.'
    agency_ids = [a[0] for a in agencies]
    customer_ids = list({a[1] for a in agencies if a[1] is not None})
    tables = get_tables(conn)

    plan = {
        'agencies': agency_ids,
        'customers': customer_ids,
        'by_agency': [],
        'by_incident': [],
        'by_unit': [],
        'by_personnel': [],
        'by_customer_user': [],
        'summary': {},
    }

    for t in tables:
        cols = get_columns(conn, t)
        if 'agency_id' in cols:
            plan['by_agency'].append(t)
        if 'incident_id' in cols and 'agency_id' not in cols:
            plan['by_incident'].append(t)
        if 'unit_id' in cols and 'agency_id' not in cols:
            plan['by_unit'].append(t)
        if 'personnel_id' in cols and 'agency_id' not in cols:
            plan['by_personnel'].append(t)
        if include_customer:
            if t == 'users' and 'customer_id' in cols:
                plan['by_customer_user'].append(t)
            elif t == 'customers' and 'id' in cols:
                plan['by_customer_user'].append(t)
            elif 'customer_id' in cols and 'agency_id' not in cols and t not in ('users', 'customers'):
                plan['by_customer_user'].append(t)

    agency_placeholders = ','.join('?' * len(agency_ids))
    customer_placeholders = ','.join('?' * len(customer_ids)) if customer_ids else ''

    def count(q, params=()):
        try:
            return conn.execute(q, params).fetchone()[0]
        except sqlite3.OperationalError as e:
            if 'no such column' in str(e):
                return 0
            raise

    for t in plan['by_agency']:
        q = f'SELECT COUNT(*) FROM "{t}" WHERE agency_id IN ({agency_placeholders})'
        plan['summary'][t] = count(q, tuple(agency_ids))
    # delete the agency rows themselves
    q = f'SELECT COUNT(*) FROM agencies WHERE id IN ({agency_placeholders})'
    plan['summary']['agencies'] = count(q, tuple(agency_ids))
    for t in plan['by_incident']:
        q = f'SELECT COUNT(*) FROM "{t}" WHERE incident_id IN (SELECT id FROM incidents WHERE agency_id IN ({agency_placeholders}))'
        plan['summary'][t] = count(q, tuple(agency_ids))
    for t in plan['by_unit']:
        q = f'SELECT COUNT(*) FROM "{t}" WHERE unit_id IN (SELECT id FROM units WHERE agency_id IN ({agency_placeholders}))'
        plan['summary'][t] = count(q, tuple(agency_ids))
    for t in plan['by_personnel']:
        q = f'SELECT COUNT(*) FROM "{t}" WHERE personnel_id IN (SELECT id FROM personnel WHERE agency_id IN ({agency_placeholders}))'
        plan['summary'][t] = count(q, tuple(agency_ids))
    if include_customer and customer_ids:
        for t in plan['by_customer_user']:
            if t == 'customers':
                q = f'SELECT COUNT(*) FROM "{t}" WHERE id IN ({customer_placeholders})'
            else:
                q = f'SELECT COUNT(*) FROM "{t}" WHERE customer_id IN ({customer_placeholders})'
            plan['summary'][t] = count(q, tuple(customer_ids))

    return plan, {'agencies': agencies, 'customer_ids': customer_ids}


def execute_purge(conn, plan, ctx, include_customer=False):
    agency_ids = [a[0] for a in ctx['agencies']]
    customer_ids = ctx['customer_ids']
    agency_placeholders = ','.join('?' * len(agency_ids))
    customer_placeholders = ','.join('?' * len(customer_ids)) if customer_ids else ''

    conn.execute('PRAGMA foreign_keys=OFF')
    try:
        for t in plan['by_incident']:
            q = f'DELETE FROM "{t}" WHERE incident_id IN (SELECT id FROM incidents WHERE agency_id IN ({agency_placeholders}))'
            conn.execute(q, tuple(agency_ids))
        for t in plan['by_unit']:
            q = f'DELETE FROM "{t}" WHERE unit_id IN (SELECT id FROM units WHERE agency_id IN ({agency_placeholders}))'
            conn.execute(q, tuple(agency_ids))
        for t in plan['by_personnel']:
            q = f'DELETE FROM "{t}" WHERE personnel_id IN (SELECT id FROM personnel WHERE agency_id IN ({agency_placeholders}))'
            conn.execute(q, tuple(agency_ids))

        for t in plan['by_agency']:
            q = f'DELETE FROM "{t}" WHERE agency_id IN ({agency_placeholders})'
            conn.execute(q, tuple(agency_ids))

        # delete the demo agency rows
        q = f'DELETE FROM agencies WHERE id IN ({agency_placeholders})'
        conn.execute(q, tuple(agency_ids))

        if include_customer and customer_ids:
            for t in plan['by_customer_user']:
                if t == 'customers':
                    q = f'DELETE FROM "{t}" WHERE id IN ({customer_placeholders})'
                else:
                    q = f'DELETE FROM "{t}" WHERE customer_id IN ({customer_placeholders})'
                conn.execute(q, tuple(customer_ids))

        conn.commit()
    finally:
        conn.execute('PRAGMA foreign_keys=ON')


def main():
    parser = argparse.ArgumentParser(description='Purge demo agency data from a SQLite database')
    parser.add_argument('--db', default=DEFAULT_DB, help='SQLite database path')
    parser.add_argument('--include-customer', action='store_true', help='Also delete the customer and user tied to demo agencies')
    parser.add_argument('--include-users', action='store_true', help='Also delete users tied to demo agencies/customer')
    parser.add_argument('--confirm', action='store_true', help='Actually delete (default is dry run)')
    parser.add_argument('--no-backup', action='store_true', help='Skip backup')
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f'Database not found: {args.db}')
        sys.exit(1)

    include_customer = args.include_customer or args.include_users
    conn = sqlite3.connect(args.db)
    try:
        plan, ctx = plan_purge(conn, include_customer=include_customer)
        if plan is None:
            print(ctx)
            sys.exit(0)

        print('Demo agencies found:')
        for a in ctx['agencies']:
            print(f'  {a[0]}: {a[2]} ({a[3]}) customer_id={a[1]}')

        print('\nDelete plan:')
        total = 0
        for t, n in sorted(plan['summary'].items()):
            if n:
                print(f'  {t}: {n} rows')
                total += n
        print(f'  TOTAL rows: {total}')

        if not args.confirm:
            print('\nDry run. Use --confirm to purge.')
            sys.exit(0)

        if not args.no_backup:
            backup = backup_db(args.db)
            print(f'\nBackup created: {backup}')

        execute_purge(conn, plan, ctx, include_customer=include_customer)
        print('\nPurge complete.')

        # Verify
        plan2, _ = plan_purge(conn, include_customer=include_customer)
        if plan2 is None:
            print('No demo agencies remain.')
        else:
            print('Verification (should be 0):')
            for t, n in sorted(plan2['summary'].items()):
                if n:
                    print(f'  {t}: {n} rows still present')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
