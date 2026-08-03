# Deployment Guide

This project is currently configured for **Render** with a **Supabase PostgreSQL** database, but the same Docker image can run on any VPS, cloud host, or PaaS that supports Docker.

## Recommended stack

- **Hosting**: [Render](https://render.com) (free/paid web service from GitHub), or any Docker host (VPS, Hetzner, DigitalOcean, Fly.io, Railway, etc.)
- **Database**: Supabase PostgreSQL (already in `.env`) or any Postgres service
- **DNS / HTTPS**: Cloudflare (put your domain in front of Render or your VPS)
- **Source control**: GitHub repo `https://github.com/dustingay87/DISPATCH-CAD.git`

## Live update workflow

1. Make changes locally and test on `http://127.0.0.1:8000`.
2. `git add` / `git commit` / `git push origin main`.
3. If you use **Render**, it will auto-deploy on every push.
4. If you self-host, run `docker compose pull && docker compose up -d` (or re-pull the image and restart).

For database-only updates without a full redeploy, run the safe `update.py` script on the live host or local copy:

```powershell
python update.py --customer customer.json
```

## Option A: Deploy on Render (quickest)

1. Push this repo to GitHub.
2. In Render, create a **Web Service** and connect the GitHub repo.
3. Render will use `render.yaml` and `Dockerfile` automatically.
4. Set these environment variables in the Render dashboard:
   - `DATABASE_URL` = your Supabase Postgres connection string
   - `SECRET_KEY` = a long random string
   - `DEFAULT_TIMEZONE` = `America/Chicago` (or your local IANA timezone)
   - `INSECURE_DEV` = `false`
5. Add any customer-specific environment variables from `.env.example`.
6. Render will build and expose the app on a public URL. You can add a custom domain and Cloudflare in front.

## Option B: Self-host with Docker Compose

1. Copy `.env.example` to `.env` and fill in at least:
   - `DATABASE_URL` for Postgres, or leave it empty to use SQLite
   - `SECRET_KEY`
   - `DEFAULT_TIMEZONE`
   - `INSECURE_DEV=false`
2. Build and run:

```bash
docker compose up -d
```

3. The app will be available on the host port defined by `PORT` (default `10000`).
4. Put a reverse proxy (nginx, Caddy, Traefik, or Cloudflare Tunnel) in front for HTTPS.

## Database updates after go-live

The app uses SQLAlchemy's `Base.metadata.create_all` plus `ensure_db_columns()` to add any missing columns automatically on startup. For complex schema migrations, use `setup_db.py` after backing up:

```powershell
python backup.py
python setup_db.py
python setup_customer.py customer.json
```

Always backup before running schema changes in production.

## Security checklist before going live

- [ ] Change `SECRET_KEY` and default admin password.
- [ ] Remove `INSECURE_DEV=true` and set it to `false`.
- [ ] Use PostgreSQL, not SQLite.
- [ ] Enable HTTPS and `SESSION_COOKIE_SECURE`.
- [ ] Restrict database credentials and never commit `.env`.
- [ ] Configure automated backups.
