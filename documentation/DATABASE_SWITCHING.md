# Database Switching Runbook

Last updated: `2026-03-17`

## Active Database Topology

- `default` (primary, read-write): Supabase Postgres
- `local` (secondary, read-only): localhost Postgres (`housing_accounting`)

This is configured in `config/settings/local.py`.

## Required Environment

Set one of these before running Django:

Preferred:

```bash
export SUPABASE_DATABASE_URL='postgresql://postgres:<YOUR_PASSWORD>@db.wmixkdfdfsoawucdbfsc.supabase.co:5432/postgres?sslmode=require'
```

Alternative:

```bash
export SUPABASE_DB_PASSWORD='<YOUR_PASSWORD>'
```

If neither is set, startup fails intentionally because Supabase is the configured primary.

### Safer Local Option (Recommended)

Create a non-tracked `.env.local` file in project root:

```bash
cat > .env.local <<'EOF'
SUPABASE_DB_PASSWORD=<YOUR_PASSWORD>
EOF
chmod 600 .env.local
```

`config/settings/local.py` auto-loads `.env.local` when present.

## Migrate Primary (Supabase)

```bash
. .venv/bin/activate
python manage.py migrate
```

## Query Local Read-Only Alias

```bash
. .venv/bin/activate
python manage.py shell
```

Then:

```python
from housing.models import Society
Society.objects.using("local").count()
```

`local` connection enforces `default_transaction_read_only=on`, so write attempts on `local` should fail at DB level.

## Notes

- Supabase direct host may fail on IPv4-only networks. If that happens, use their session pooler connection string.
- Keep passwords out of committed files; use environment variables only.
