"""
Keep every Mehnatana table inside its own PostgreSQL schema.

The Supabase project is shared with other applications. Each new connection
switches to settings.DB_SCHEMA (default "mehnatana"), creating it the first
time, so Django's own tables (django_migrations, auth_*, sessions) and ours
(all named mtn_*) never touch tables in the public schema. A schema that is
not "public" is also not exposed through Supabase's REST API.
"""
from django.conf import settings


def use_app_schema(sender, connection, **kwargs):
    schema = getattr(settings, "DB_SCHEMA", "")
    if connection.vendor != "postgresql" or not schema:
        return
    quoted = connection.ops.quote_name(schema)
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", [schema])
        if cursor.fetchone() is None:
            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {quoted}")
        cursor.execute(f"SET search_path TO {quoted}")
