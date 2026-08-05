#!/usr/bin/env python3
"""Purge demo/pilot data from the live database."""
import os
from dotenv import load_dotenv
load_dotenv()
from sqlalchemy import text
from app import SessionLocal


def get_ids(session, table, where_col, where_ids):
    if not where_ids:
        return []
    rows = session.execute(
        text(f'SELECT id FROM {table} WHERE {where_col} = ANY(:ids)'),
        {'ids': where_ids}
    ).fetchall()
    return [r[0] for r in rows]


def main():
    session = SessionLocal()
    try:
        customer_ids = get_ids(session, 'customers', 'name', ['Default Customer', 'Metro County CAD'])

        agency_ids = get_ids(session, 'agencies', 'customer_id', customer_ids)
        agency_ids += [r[0] for r in session.execute(text(
            "SELECT id FROM agencies WHERE name = ANY(:n) OR domain = ANY(:d)"
        ), {
            'n': ['City Police', 'Metro Police', 'Metro Fire', 'County EMS', 'Demo Fire'],
            'd': ['pilot.police', 'pilot.fire', 'pilot.ems']
        }).fetchall()]
        agency_ids = list(set(agency_ids))

        if not customer_ids and not agency_ids:
            print('No demo data found.')
            return

        derived = {
            'incident_id': get_ids(session, 'incidents', 'agency_id', agency_ids),
            'unit_id': get_ids(session, 'units', 'agency_id', agency_ids),
            'personnel_id': get_ids(session, 'personnel', 'agency_id', agency_ids),
            'user_id': get_ids(session, 'users', 'customer_id', customer_ids) +
                       get_ids(session, 'users', 'agency_id', agency_ids),
            'destination_id': get_ids(session, 'destinations', 'agency_id', agency_ids),
            'location_id': get_ids(session, 'locations', 'agency_id', agency_ids),
            'certification_id': get_ids(session, 'certifications', 'agency_id', agency_ids),
            'scheduled_event_id': get_ids(session, 'scheduled_events', 'agency_id', agency_ids),
            'scheduled_transport_id': get_ids(session, 'scheduled_transports', 'agency_id', agency_ids),
            'standing_order_id': get_ids(session, 'standing_orders', 'agency_id', agency_ids),
            'post_zone_id': get_ids(session, 'post_zones', 'agency_id', agency_ids),
        }

        columns_to_delete = {'customer_id': customer_ids, 'agency_id': agency_ids}
        columns_to_delete.update(derived)

        session.execute(text("SET session_replication_role = 'replica';"))

        for col, ids in columns_to_delete.items():
            if not ids:
                continue
            tables = session.execute(text(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name = :c"
            ), {'c': col}).scalars().all()
            for table in tables:
                if col == 'customer_id' and table == 'customers':
                    continue
                if col == 'agency_id' and table == 'agencies':
                    continue
                session.execute(text(f"DELETE FROM {table} WHERE {col} = ANY(:ids)"), {'ids': ids})

        if agency_ids:
            session.execute(text("DELETE FROM agencies WHERE id = ANY(:ids)"), {'ids': agency_ids})
        if customer_ids:
            session.execute(text("DELETE FROM customers WHERE id = ANY(:ids)"), {'ids': customer_ids})

        session.commit()
        session.execute(text("SET session_replication_role = 'origin';"))
        print(f'Purged {len(customer_ids)} demo customer(s): {customer_ids}')
        print(f'Purged {len(agency_ids)} demo agency(ies): {agency_ids}')
    except Exception as e:
        session.rollback()
        try:
            session.execute(text("SET session_replication_role = 'origin';"))
        except Exception:
            pass
        print('Error:', e)
        raise
    finally:
        session.close()


if __name__ == '__main__':
    main()
