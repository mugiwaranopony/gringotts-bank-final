#!/usr/bin/env bash
# Render runs this script every time it deploys the app.
# `set -o errexit` stops at the first failing command, so a broken build
# is never allowed to go live on top of a working one.
set -o errexit

pip install -r requirements.txt

# Gather every static file into STATIC_ROOT so WhiteNoise can serve them.
python manage.py collectstatic --no-input

# Apply any new migrations to the PostgreSQL database.
python manage.py migrate

# Free Render services have no shell, so this is where the demo users and
# the admin account get created. Safe on every deploy: it skips anyone
# who already exists.
python manage.py seed_users
