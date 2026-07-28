# VolCAD Prototype

Modern, browser-based Computer-Aided Dispatch (CAD) prototype for volunteer fire/EMS/EMS and small public-safety agencies.

## Features

- **Per-discipline dispatcher consoles**: `/console`, `/police`, `/fire`, `/ems`
- **Call intake**: `/call-entry` with agency-specific call types and priorities
- **AVL / map**: unit markers, incident markers, unit breadcrumb trails, and critical-layer toggles
- **MDT field screen**: `/mdt?unit_id=1` with status updates, active incident, and two-way messaging
- **Unit recommendation engine**: response plan + distance based suggestions
- **Call history & timeline**: `/history` with discipline/agency filters and full audit trail
- **Roster management**: `/roster` for units and personnel
- **CSV import**: `/import` for agencies, units, personnel, incidents
- **Reporting dashboard**: `/reports` with KPIs, SLA compliance, status/priority breakdowns
- **Admin config**: `/admin` with per-discipline seed templates
- **Audit/event ledger**: `/events` captures all key actions

## Quick start (Windows)

```powershell
cd C:\Users\Dustin\CascadeProjects\volcad-prototype
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
uvicorn app:app --reload
```

Open `http://127.0.0.1:8000` and log in with the auto-created admin user.

## Default admin

If the `users` table is empty on startup, an admin is created automatically:

- Email: `dustin@dispatchtodiscipleship.net`
- Password: `Warrior/202601!`

Change this in `app.py` `seed_default_admin` before going live.

## Environment variables

See `.env.example`.

- `DATABASE_URL` - defaults to SQLite `sqlite:///./volcad.db` for local development
- `SECRET_KEY` - used for HMAC session tokens
- `SUPABASE_DB_URL` or `DATABASE_URL` for PostgreSQL on Render/Supabase

## Render deployment

1. Push to GitHub.
2. Create a Web Service on Render, point it at this repo.
3. Set the build/start commands:
   - Build: `pip install -r requirements.txt`
   - Start: `uvicorn app:app --host 0.0.0.0 --port $PORT`
4. Add environment variables (`DATABASE_URL`, `SECRET_KEY`).

## Pilot workflow

1. Log in at `/login`.
2. Open `/admin` and click **Seed Full Pilot Dataset** to create sample police, fire, and EMS agencies.
3. Open `/call-entry` to create a new call.
4. Open `/console` to dispatch recommended units.
5. Open `/mdt?unit_id=1` to simulate a unit updating status.
6. Open `/history` to view past calls and timelines.
7. Open `/reports` to view KPIs.

## CSV import format

### Agencies
`name,agency_type,city,state,domain`

### Units
`agency_id,call_sign,name,unit_type,lat,lng,taip_id`

### Personnel
`agency_id,first_name,last_name,email,phone,sms_phone,current_unit_id,duty_status`

### Incidents
`agency_id,call_number,call_type,priority,location_text,lat,lng,status,caller_name,callback,narrative`

## Production hardening checklist

- [ ] Change `SECRET_KEY` and default admin credentials.
- [ ] Switch `DATABASE_URL` to PostgreSQL and run `schema.sql`.
- [ ] Enable HTTPS and set `SESSION_COOKIE_SECURE=True`.
- [ ] Configure automated database backups.
- [ ] Add rate limiting and request logging.
- [ ] Move sensitive endpoints behind admin or agency roles.
- [ ] Implement off-site log shipping and audit review.

## License

MIT - for prototype / pilot use.
