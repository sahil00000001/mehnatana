#!/bin/sh
# Container start: bring the database up to date, collect static files, start Gunicorn.
set -eu
python manage.py migrate --noinput
python manage.py collectstatic --noinput
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120 --access-logfile -
