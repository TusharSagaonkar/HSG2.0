# Render Deployment Runbook

Last updated: `2026-03-17`

## Target Topology

- Web app: Render web service
- Email queue automation: Render cron job running every minute
- Primary database: Supabase Postgres
- Cache: optional Render Key Value (`REDIS_URL`) or in-memory cache fallback

## Files Involved

- `render.yaml`
- `config/settings/production.py`

## Render Setup

1. Push the repository to GitHub/GitLab.
2. In Render, create a new Blueprint and point it at this repository.
3. Render will read `render.yaml` and create:
   - the `housing-accounting` web service
   - the `housing-accounting-email-queue` cron job
4. When prompted for environment variables marked `sync: false`, provide:

```text
SUPABASE_DATABASE_URL=postgresql://postgres:<YOUR_PASSWORD>@db.wmixkdfdfsoawucdbfsc.supabase.co:5432/postgres
```

Optional if you prefer Supabase's pooler:

```text
SUPABASE_POOLER_URL=<your_supabase_pooler_connection_string>
```

Optional if you add Render Key Value:

```text
REDIS_URL=<your_render_key_value_connection_string>
```

## Required Render Environment

These are defined in `render.yaml`:

- `DJANGO_SETTINGS_MODULE=config.settings.production`
- `DJANGO_DEBUG=False`
- `DJANGO_ALLOWED_HOSTS=housing-accounting.onrender.com`
- `DJANGO_CSRF_TRUSTED_ORIGINS=https://housing-accounting.onrender.com`
- `USE_SUPABASE_DB=true`

Render auto-generates:

- `DJANGO_SECRET_KEY`

## Deploy Behavior

- Build command installs dependencies and runs `collectstatic`.
- Start command runs `migrate` and then starts Gunicorn.
- The cron job runs `python manage.py process_email_queue --limit 50` every minute.
- Render cron schedules use UTC.
- On the free plan, migrations remain in `startCommand` because Render reserves `preDeployCommand` for paid web services.
- Render cron jobs do not support the `free` plan, so the email scheduler is configured on `starter`.

## Custom Domain Checklist

If you later add a custom domain:

1. Add the domain in Render.
2. Update `DJANGO_ALLOWED_HOSTS`.
3. Update `DJANGO_CSRF_TRUSTED_ORIGINS` with the full `https://...` origin.
4. Redeploy.

## Verification

After the first deploy:

1. Open the Render service URL.
2. Open `/admin/`.
3. Confirm database-backed pages load successfully.
4. Review web service logs to confirm migrations completed cleanly.
5. Open the `housing-accounting-email-queue` cron job in Render.
6. Trigger a manual run once to verify `Processed ... queued emails.` appears in the logs.
7. Confirm new `PENDING` or `RETRY` emails move forward automatically on the next scheduled minute.
