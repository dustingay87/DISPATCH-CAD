---
description: Safely update VolCAD code and database schema without losing customer data
---

# Safe update workflow

Run this workflow before every deploy or schema change to protect customer data.

## 1. Open a terminal in the project root

```powershell
cd C:\Users\Dustin\CascadeProjects\volcad-prototype
```

## 2. Make sure `.env` contains the database URL

`SUPABASE_DB_URL` or `DATABASE_URL` must be set.

## 3. Run the safe update script

// turbo
```powershell
python update.py --customer customer.json
```

What it does:

1. `git pull`
2. `python backup.py` - writes a full database backup to `backups/`
3. `python setup_db.py` - applies only additive, non-destructive `schema.sql` statements
4. `python setup_customer.py customer.json` - upserts customer config without deleting existing data

## 4. If something goes wrong, restore from the backup in `backups/`

For PostgreSQL:

```powershell
psql $env:SUPABASE_DB_URL -f backups\<timestamp>_volcad.sql
```

For SQLite:

```powershell
copy backups\<timestamp>_volcad.db volcad.db
```

## Important rules

- Never run `setup_db.py` without first running `backup.py`.
- `setup_db.py` will refuse `DROP`, `TRUNCATE`, and `DELETE FROM` statements unless `--force` is used.
- Only use `--force` after a confirmed backup and a code review of `schema.sql`.
