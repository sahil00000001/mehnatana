# Running Mehnatana

`README.md` is the product specification. This file explains how to run the app, connect it to Supabase, deploy it and back it up.

## 1. On your own computer

You need Python 3.12 or newer.

**Windows (PowerShell):**

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env          # then open .env and set DEBUG=True
python manage.py migrate        # creates the tables and the statutory rates
python manage.py seed_demo      # two demo businesses; prints their logins once
python manage.py createsuperuser
python manage.py runserver
```

**Linux or macOS:** the same commands, but use `source .venv/bin/activate` and `cp .env.example .env`.

Then open:

- http://127.0.0.1:8000/ to log in as a demo owner. Shri Ram Kirana Stores' login is printed by `seed_demo`.
- http://127.0.0.1:8000/admin/ to log in as the platform admin.

If `DATABASE_URL` and `POSTGRES_HOST` are empty, the data is kept in a local `db.sqlite3` file.

Run the tests (225 tests, including all README §17 numbers):

```
python manage.py test
```

`python manage.py seed_demo --reset` deletes the two demo businesses and creates them again, and
`python manage.py seed_demo --delete` removes them.

## 2. Connecting to Supabase

The app connects straight to Supabase's PostgreSQL database, so it needs the **database connection string**, not the API keys (anon or service_role).

1. In Supabase, open **Project Settings → Database → Connection string**.
2. Choose **Session pooler** (port 5432) and copy the URI.
3. In `.env`, paste it as `DATABASE_URL`. Put the database password in place of `[YOUR-PASSWORD]` and add `?sslmode=require` at the end:
   ```
   DATABASE_URL=postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
   POSTGRES_SCHEMA=mehnatana
   ```
   If the password contains `@`, `:`, `/` or `#`, write it URL-encoded: `@` becomes `%40`, `#` becomes `%23`.
4. Create the tables, then a platform admin:
   ```
   python manage.py migrate
   python manage.py createsuperuser
   ```

**How the tables stay separate from your other tables:**

- Everything goes into its own schema, `mehnatana`, which is created automatically on the first connection.
- Every Mehnatana table name also starts with `mtn_`.
- Nothing is created or changed in `public`, or in any other schema.
- In the Supabase Table Editor, pick the `mehnatana` schema to see the tables.
- **Do not** add `mehnatana` to *API → Exposed schemas*. Keeping it unexposed means the payroll data cannot be read through Supabase's REST API.

**Demo businesses on the live site.** GitHub → Actions → *Demo businesses* → Run workflow, then choose
`create`, `reset` or `delete`. It adds Shri Ram Kirana Stores (login `9876500001`) and Annapurna Sweets
(login `9876500002`) with a draft November 2026. The repository is public, so the job never prints a password:
give the two logins a password from `/admin/` → Logins → tick them → *Reset password*. Only businesses
whose LIN starts with `DEMO-LIN-` are ever reset or deleted, so a real business is never touched.
Delete the demo businesses before real use.

## 3. Starting a new business (platform admin)

1. Go to `/admin/` → **Companies → Add**. Fill the code (3–6 capital letters), names, address and state, and the first salary month.
2. Tick the company in the list, then choose the action **Create owner login**. The username and a temporary password are shown once; give them to the owner. The owner must choose a new password at the first login.
3. The owner then works from Settings → Payroll rules, Employees → Upload from Excel, and Monthly Salary.

To reset a forgotten password or unlock a login, open **Logins**, tick it, and use the action **Reset password** or **Unlock**.

Statutory rates, PT slabs, tax slabs and due dates are under **Statutory tables**. When the law changes, add a new row with its "effective from" date; do not edit the old row. Every admin change is written to the **Audit log**.

## 4. Vercel (live now)

The app runs at **https://mehnatana.vercel.app** (Vercel project `mehnatana`, region Mumbai `bom1`,
next to the Supabase database in `ap-south-1`).

- **Deploys:** every push to `main` on GitHub deploys to production automatically (`vercel.json`).
- **Database:** every push to `main` also runs the GitHub Actions job *Tests and database*
  (`.github/workflows/database.yml`): all tests on SQLite, then `migrate` on Supabase and, once, the
  platform admin login (`ensure_admin`). It can also be started by hand: GitHub → Actions → Run workflow.
- **GitHub secrets:** `DATABASE_URL`, `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_PASSWORD`.
- **Vercel environment variables (Production):** `DEBUG=False`, `SECRET_KEY`, `ALLOWED_HOSTS=.vercel.app`,
  `DATABASE_URL` (Supabase Session pooler), `POSTGRES_SCHEMA=mehnatana`, `CONN_MAX_AGE=0`,
  `FILE_STORAGE=database`, `WHITENOISE_USE_FINDERS=True`, `MAX_UPLOAD_MB=4`, `SUPPORT_PHONE`, `TIME_ZONE`.
  Change them in Vercel → Project → Settings → Environment Variables, then redeploy.
- **Files on Vercel** (logos, uploaded sheets) are kept in the database table `mtn_stored_file`, because Vercel
  has no lasting disk. They are private, like the media folder, and included in database backups.
- **If you change the database password** in Supabase: update `DATABASE_URL` in both Vercel and GitHub secrets.
- **Custom domain:** add it in Vercel → Project → Settings → Domains, then add it to `ALLOWED_HOSTS` and
  `CSRF_TRUSTED_ORIGINS` (for example `https://payroll.example.com`).
- Uploads are limited to 4 MB on Vercel (Vercel accepts up to 4.5 MB per request).

## 5. Server (one small VPS) — the alternative to Vercel

1 vCPU / 2 GB RAM is enough to start. Install Docker first.

1. Copy the project to the server, e.g. `/opt/mehnatana`.
2. Create `.env` from `.env.example` and set:
   - `DEBUG=False`
   - `SECRET_KEY` (a long random string)
   - `ALLOWED_HOSTS=payroll.example.com`
   - `CSRF_TRUSTED_ORIGINS=https://payroll.example.com`
   - `DOMAIN=payroll.example.com`
   - `DATABASE_URL` (Supabase, as in section 2)
   - `SUPPORT_PHONE`
3. Point the domain's DNS at the server, then run:
   ```
   docker compose up -d
   docker compose exec web python manage.py createsuperuser
   ```

What runs:

- `web`: Django under Gunicorn with 3 workers. It runs `migrate` and `collectstatic` every time it starts.
- `caddy`: gets the HTTPS certificate automatically and allows uploads up to 10 MB.
- Uploaded sheets and logos are kept on the `media` volume. Caddy never serves them; the app does, after checking the company.

To use a local PostgreSQL 16 instead of Supabase:

- Leave `DATABASE_URL` empty.
- Set `POSTGRES_HOST=db`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` and `POSTGRES_SSLMODE=disable`.
- Run `docker compose --profile localdb up -d`.

To update the app: copy the new code, then run `docker compose up -d --build`.

## 6. Backups

`scripts/backup.sh` saves:

- the `mehnatana` schema, with `pg_dump --schema=mehnatana`, as `db-<date>.dump`;
- the media folder, as `media-<date>.tar.gz`.

Both go to `/var/backups/mehnatana`, and files older than 30 days are deleted. Run it every night from cron:

```
30 2 * * *  cd /opt/mehnatana && ./scripts/backup.sh >> /var/log/mehnatana-backup.log 2>&1
```

Also copy that folder off the server, for example to object storage with `rclone` (see the end of the script). Supabase's own daily backups cover the whole project, but they do not include the media folder.

Payroll records must be kept for at least 5 years. Keep old monthly dumps somewhere safe before the 30-day cleanup deletes them.

### Restoring

**Check a dump first** by restoring it into an empty test database:

```
docker run --rm -v /var/backups/mehnatana:/b postgres:17 \
  pg_restore --dbname "postgresql://...test-db..." --no-owner /b/db-20261201-0230.dump
```

**Database (replaces the live `mehnatana` schema):** stop the app first with `docker compose stop web`, then:

```
docker run --rm -v /var/backups/mehnatana:/b postgres:17 \
  pg_restore --dbname "$DATABASE_URL" --no-owner --clean --if-exists /b/db-20261201-0230.dump
```

**Uploaded files:**

```
docker compose run --rm --no-deps -v /var/backups/mehnatana:/backup --entrypoint "" web \
  tar xzf /backup/media-20261201-0230.tar.gz -C /app
```

Then start the app again with `docker compose start web`.

## 7. Before going live

- Have a Chartered Accountant check README §8 and §9 against your customers' states (see README §21).
- Compare the ESI upload file's columns with the current template on the ESIC portal.
- Add PT slabs in the admin for any state other than Karnataka and Maharashtra.
