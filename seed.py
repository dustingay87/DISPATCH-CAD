import app


def main():
    db = app.SessionLocal()
    try:
        if db.query(app.Agency).first():
            print('Database already seeded.')
            return

        agency = app.Agency(
            name='Dustinsburg Volunteer Fire & EMS',
            agency_type='fire_ems',
            domain='dustinsburg-vfd',
            approved=True,
            lat=39.8283,
            lng=-98.5795
        )
        db.add(agency)
        db.commit()
        db.refresh(agency)

        station = app.Location(
            agency_id=agency.id,
            name='Station 1',
            location_type='station',
            address='123 Main St',
            lat=39.8283,
            lng=-98.5795
        )
        db.add(station)
        db.commit()
        db.refresh(station)

        engine = app.Unit(
            agency_id=agency.id,
            name='Engine 1',
            call_sign='E1',
            unit_type='engine',
            taip_id='TAIP001',
            current_status='AQ',
            current_lat=39.8283,
            current_lng=-98.5795
        )
        medic = app.Unit(
            agency_id=agency.id,
            name='Medic 1',
            call_sign='M1',
            unit_type='ambulance',
            taip_id='TAIP002',
            current_status='AQ',
            current_lat=39.8283,
            current_lng=-98.5795
        )
        db.add_all([engine, medic])
        db.commit()
        db.refresh(engine)
        db.refresh(medic)

        jane = app.Personnel(
            agency_id=agency.id,
            first_name='Jane',
            last_name='Doe',
            radio_id='R101',
            duty_status='on_duty',
            current_unit_id=engine.id
        )
        db.add(jane)
        db.commit()

        incident = app.Incident(
            agency_id=agency.id,
            incident_number='2026-00001',
            call_type='structure_fire',
            priority=1,
            status='open',
            location_text='456 Oak Ave',
            lat=39.8290,
            lng=-98.5780,
            caller_name='John Smith',
            callback='555-0100',
            narrative='Smoke visible from the roof of a two-story residence'
        )
        db.add(incident)
        db.commit()
        db.refresh(incident)

        db.add(app.CallLog(
            incident_id=incident.id,
            log_type='note',
            message='Caller reports heavy smoke from the roof; all occupants out of the house.'
        ))
        db.commit()

        print('Seed complete.')
    finally:
        db.close()


if __name__ == '__main__':
    main()
