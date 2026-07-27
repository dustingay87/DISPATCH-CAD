# VolCAD Prototype

Lightweight CAD prototype for volunteer fire/EMS and no-budget public-safety agencies.

## Files

- `app.py` - FastAPI application, SQLAlchemy ORM models, TAIP parser, and dispatch endpoints (SQLite for prototyping).
- `schema.sql` - Production-ready PostgreSQL/PostGIS DDL.
- `seed.py` - Sample agency, units, personnel, and incident.
- `requirements.txt` - Python dependencies.

## Quick start (Windows)

```powershell
cd C:\Users\Dustin\CascadeProjects\volcad-prototype
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python seed.py
uvicorn app:app --reload
```

Open `http://127.0.0.1:8000/docs` for interactive API documentation.

## Example flow

1. Seed creates `Dustinsburg Volunteer Fire & EMS` with `Engine 1` (taip_id `TAIP001`) and `Medic 1`.
2. List units: `GET /units?agency_id=1`
3. Create an incident: `POST /incidents` with an `IncidentCreate` body.
4. Dispatch a unit: `POST /incidents/2/dispatch/1`
5. Update unit status: `POST /incidents/2/units/1/status` with `{ "status_code": "ER" }`
6. Ingest TAIP position: `POST /taip/ingest` with `{ "raw": "id=TAIP001;lat=39.8291;lon=-98.5785;spd=35;hdg=270" }`

## Production migration path

1. Replace `sqlite:///./volcad.db` in `app.py` with `postgresql+psycopg2://...`.
2. Run `schema.sql` against PostgreSQL with PostGIS enabled.
3. Switch `Float` lat/lng columns to `Geometry(Point, 4326)` via GeoAlchemy2 or `func.ST_GeogFromText`.

## Status codes used

- `AQ` - available in quarters
- `AK` - acknowledged / dispatched
- `ER` - en route
- `OS` - on scene
- `TR` - transporting patient
- `ED` - en route to destination
- `CAN` - cancelled / clear
