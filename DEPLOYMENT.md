# 🚀 Deploying Gringotts Bank to the Real Internet

A step-by-step guide to putting your bank online with
**[Render](https://render.com)**, whose free plan needs no credit card.

Work through it at your own pace. Type the code — do not copy-paste. When
something breaks (it will), read the error from the **bottom up**.

---

## Why can't we just upload it?

Everything so far has run with `python manage.py runserver`, which prints
this warning every single time:

```
WARNING: This is a development server. Do not use it in a production setting.
```

Django is not being dramatic. Four things about our project are perfectly
fine for learning and genuinely dangerous in public:

| # | Problem | Why it matters |
|---|---------|----------------|
| 1 | The **secret key** is written in `settings.py` | It signs session cookies. Anyone who knows it can forge a login as *any* user, including `admin`. And `settings.py` is on GitHub. |
| 2 | **`DEBUG = True`** | Cause any error and Django shows the visitor your source code, file paths, and settings. |
| 3 | The database is a **file** (`db.sqlite3`) | Render gives each deploy a fresh, empty filesystem. Every push would delete all your customers and their money. |
| 4 | **Nobody serves the CSS** | `runserver` serves static files as a favour during development. A real server does not. Your bank would arrive with no styling at all. |

So deploying is not "click a button". It is fixing those four things.

**One rule we will not break:** your laptop must keep working exactly as it
does now — SQLite, `DEBUG = True`, no setup. Every change below is
*conditional*: production behaviour switches on only when the app notices it
is running on Render.

---

## Step 1 — Install the production packages

With your virtual environment active:

```shell
(.venv) $ python -m pip install gunicorn whitenoise "psycopg[binary]" dj-database-url
```

| Package | Its job |
|---------|---------|
| **gunicorn** | The real web server that runs Django. Replaces `runserver`. |
| **whitenoise** | Serves your CSS and images → fixes problem 4. |
| **psycopg[binary]** | The driver Python uses to speak to PostgreSQL → part of problem 3. |
| **dj-database-url** | Turns an address like `postgresql://user:pass@host/name` into the settings dictionary Django expects. |

> 💡 **Why the quotes around `"psycopg[binary]"`?** The square brackets mean
> "install psycopg *and* its ready-compiled driver". Some shells treat `[` as
> a special character, so the quotes say "this is just text". The `[binary]`
> part is what saves you from needing a C compiler.

## Step 2 — Record them in `requirements.txt`

Render will not have your laptop. It builds your app on an empty machine, and
the **only** way it knows what to install is this file.

```shell
(.venv) $ pip freeze > requirements.txt
(.venv) $ cat requirements.txt
```

`pip freeze` asks your virtualenv "what is installed, at exactly which
version?" and `>` writes that answer into the file.

You should see about a dozen lines, including `dj-database-url`, `gunicorn`,
`psycopg`, `psycopg-binary`, and `whitenoise`. A couple of extras like
`packaging` and `typing_extensions` may appear — those are helpers the new
packages depend on. Leave them.

> 💡 **Why pin exact versions** (`Django==6.0.7`, not just `Django`)? So
> Render installs the *same* Django you tested on. Without pinning, a new
> version could be released tonight and tomorrow's deploy would install
> something you have never run. "It works on my machine" starts exactly there.

## Step 3 — Teach `settings.py` where it is

This is the heart of the whole thing.

**The problem:** we need `DEBUG = False`, a real secret, and PostgreSQL **on
Render** — but `DEBUG = True`, no secret, and SQLite **on our laptop**. One
file, two behaviours. How can the file know where it is running?

**The answer: an environment variable** — a value that lives *outside* your
code, in the environment the program is started in. Render sets a variable
called `RENDER` on everything it runs. Your laptop does not have it. So the
app can simply ask.

This is also where secrets belong: outside the source, so they are never in
git.

### 3a. The top of the file

Open `django_project/settings.py`. **Replace** this:

```python
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = "django-insecure-...your own key..."

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = []
```

**with** this — keeping *your own* `django-insecure-...` key:

```python
import os
from pathlib import Path

import dj_database_url

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Render sets a RENDER environment variable on every service it runs, so this
# is how the app knows whether it is on your laptop or on the real internet.
IS_PRODUCTION = "RENDER" in os.environ

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-...your own key...",
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = not IS_PRODUCTION

# Which domain names are we willing to answer to? Render tells us ours.
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
CSRF_TRUSTED_ORIGINS = []

RENDER_EXTERNAL_HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)
    # Django checks that POST requests came from a page it served. Behind
    # Render's HTTPS proxy it needs to be told the site is https://.
    CSRF_TRUSTED_ORIGINS.append(f"https://{RENDER_EXTERNAL_HOSTNAME}")
```

Read this line out loud, because it is the whole idea:

```python
os.environ.get("DJANGO_SECRET_KEY", "django-insecure-...")
```

*"Give me the environment variable `DJANGO_SECRET_KEY`. If there isn't one,
use this development key instead."* On Render there is one → the real key
wins. On your laptop there isn't → nothing changes for you. **That is why
local development keeps working.**

`ALLOWED_HOSTS` is Django's guest list of domain names. With `DEBUG = False`
and an empty list, Django refuses **every** request. Render tells us our
address in `RENDER_EXTERNAL_HOSTNAME`, so we add it when it exists.

### 3b. WhiteNoise middleware

Add **one line** to `MIDDLEWARE`, directly under `SecurityMiddleware`:

```python
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise serves our CSS in production. It must sit directly below
    # SecurityMiddleware and above everything else.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    # ...leave the rest exactly as it is...
]
```

**Middleware** is a stack of layers every request passes through, **in
order**: in through each layer, to your view, and back out again.

**Why position matters:** WhiteNoise goes second so a request for `base.css`
is answered *immediately*, without waking up sessions, authentication, and
the messages framework just to hand over a stylesheet.

### 3c. The database

Replace the `DATABASES` block:

```python
# Render gives us a DATABASE_URL for the PostgreSQL database. With no such
# variable -- on your laptop -- we fall back to the same SQLite file as always.
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
    )
}
```

Same trick: use `DATABASE_URL` if it exists, otherwise SQLite.

> 🎓 **This is the payoff for migrations.** We are swapping the entire
> database engine. How much of our code has to change? **None.** Not one line
> of `models.py`, not one view — because you never wrote SQL. You wrote
> models, and `makemigrations` translated them. The same migration files that
> built your SQLite tables will build PostgreSQL tables untouched.

`conn_max_age=600` means "keep a database connection open for 10 minutes
instead of opening a fresh one per request". SQLite is just a file, so
opening it is instant; PostgreSQL lives on another machine across a network,
where opening a connection is slow.

### 3d. Static files

Under `STATIC_URL`, add:

```python
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

# `collectstatic` copies every static file from every app into this one
# folder, which is the only folder WhiteNoise serves from.
STATIC_ROOT = BASE_DIR / "staticfiles"
```

**There are now two static folders. Do not mix them up:**

| Folder | Who writes it | In git? |
|--------|---------------|---------|
| `static/` | **you** — your `base.css` | ✅ yes |
| `staticfiles/` | **Django** — built by `collectstatic` | ❌ no |

`collectstatic` gathers your CSS **plus** the admin panel's CSS **plus**
crispy-forms' files into one folder, so WhiteNoise has a single place to
serve from. It is generated output, like a compiled file, so add it to
`.gitignore`:

```
# .gitignore
.venv/
__pycache__/
db.sqlite3
*.pyc
staticfiles/
```

### 3e. The production-only block

At the very **bottom** of `settings.py`:

```python
# Extra locks for the real internet only. Turning these on locally would
# just redirect you to an https://127.0.0.1 that does not exist.
if IS_PRODUCTION:
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }

    # Render terminates HTTPS and forwards plain HTTP, so Django must read
    # this header to know the visitor really is on https. Without it,
    # SECURE_SSL_REDIRECT causes an endless redirect loop.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
```

Three of these are the ones people get wrong:

**`CompressedManifestStaticFilesStorage`** renames `base.css` to something
like `base.087e27e934e1.css`, where the hash comes from the file's contents.
Change one character of CSS and the name changes, so browsers fetch the new
version instantly instead of serving a stale cached copy for a week. But it
refuses to serve any file missing from the manifest that `collectstatic`
builds — and on your laptop there is no manifest. **Put this outside the
`if` and every one of your tests fails** with:

```
ValueError: Missing staticfiles manifest entry for 'css/base.css'
```

**`SECURE_PROXY_SSL_HEADER` must accompany `SECURE_SSL_REDIRECT`.** Render
handles HTTPS at its front door and forwards plain HTTP to your app. Without
this header, Django sees "http", redirects to https, Render forwards http
again, Django redirects again… forever.

**`STORAGES` needs both keys.** Setting it replaces the whole dictionary, so
listing only `staticfiles` silently deletes the `default` file storage.

> ⚠️ **Do not copy settings from older tutorials.** Nearly every deployment
> guide online — including Render's own — still says
> `STATICFILES_STORAGE = "whitenoise.storage..."`. That setting was **removed
> in Django 5.1**. On Django 6 it does nothing, your CSS silently breaks, and
> no error ever mentions it. `STORAGES` is the modern replacement.

## Step 4 — The build script

Render needs to know how to prepare your app. Create `build.sh` next to
`manage.py`:

```bash
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
```

Now make it **executable**, or Render refuses to run it:

```shell
# Ubuntu / macOS
(.venv) $ chmod +x build.sh

# Windows (PowerShell)
(.venv) $ git update-index --chmod=+x build.sh
```

> 💡 Windows filesystems have no "executable" bit, so `chmod` does not exist
> there. The git command sets the bit *inside git*, which is what Render
> reads. Skip this and the deploy dies with
> `bash: ./build.sh: Permission denied` — a very confusing five minutes.

You can verify it worked:

```shell
(.venv) $ git ls-files -s build.sh
100755 ... build.sh      # 100755 = executable. 100644 = not.
```

## Step 5 — Protect the admin account

Look at that last line of `build.sh` again.

On a normal server you would create the admin by opening a shell and running
`createsuperuser`. **Render's free plan has no shell** — that is a paid
feature. So the build script is our only chance, and `seed_users` already
does exactly that.

But there is a problem: `seed_users` gives `admin` the password
`testpass123`, which is written in a public tutorial. Anyone who read it
could log into your live admin and give themselves a million dollars.

In `banking/management/commands/seed_users.py`:

```python
import os

# ...

DEMO_USERS = ["alice", "bob", "harry", "hermione", "ron"]
PASSWORD = "testpass123"

# The demo customers keep their well-known password -- they only hold
# pretend money. The superuser can edit anyone's balance, and this password
# is printed in a public tutorial, so in production it comes from the
# environment instead.
ADMIN_PASSWORD = os.environ.get("DJANGO_ADMIN_PASSWORD", PASSWORD)
```

And use it where the superuser is created:

```python
        if not User.objects.filter(username="admin").exists():
            User.objects.create_superuser("admin", "admin@email.com", ADMIN_PASSWORD)
```

`alice` and friends keep `testpass123` **on purpose** — visitors should be
able to log in and try a transfer, and they only hold pretend money. `admin`
gets a real password that exists only on Render.

## Step 6 — Rehearse production on your own machine

Never let a deploy be the first time your production settings run. A failure
here takes ten seconds to read; the same failure on Render takes five minutes
to reproduce.

First, prove ordinary development still works:

```shell
(.venv) $ python manage.py test
```

All green. If instead you get a pile of `Missing staticfiles manifest entry`
errors, you put `STORAGES` outside the `if IS_PRODUCTION:` block.

Now pretend to be Render:

```shell
# Ubuntu / macOS
(.venv) $ RENDER=1 DJANGO_SECRET_KEY=fake-key python manage.py collectstatic --no-input

# Windows (PowerShell)
(.venv) $ $env:RENDER=1; $env:DJANGO_SECRET_KEY="fake-key"
(.venv) $ python manage.py collectstatic --no-input
```

```
131 static files copied to '.../staticfiles', 393 post-processed.
```

"Post-processed" is the hashing and compressing. Look in `staticfiles/css/`
and you will find `base.css`, `base.087e27e934e1.css`, and `base.css.gz` —
the original, the fingerprinted copy, and the compressed one.

Then run Django's deployment checklist:

```shell
(.venv) $ RENDER=1 DJANGO_SECRET_KEY=fake-key RENDER_EXTERNAL_HOSTNAME=example.onrender.com python manage.py check --deploy
```

You should see exactly two warnings:

- **`security.W009`** — your `SECRET_KEY` is too short. Correct: we passed a
  fake one. The real key on Render will be long and random.
- **`security.W004`** — `SECURE_HSTS_SECONDS` is not set. **We leave this on
  purpose.** HSTS tells every browser "never speak plain HTTP to this domain
  again for the next N seconds", and browsers obey even if you change your
  mind. It is a real production setting, but not one to switch on blind.

Reading a warning and deciding *"understood, not now"* is a normal part of
shipping software. Do not silence warnings you have not read.

Finally, run the real server:

```shell
(.venv) $ gunicorn django_project.wsgi:application
```

Visit http://127.0.0.1:8000 — styled home page, served by gunicorn and
WhiteNoise. `Ctrl+C` to stop.

## Step 7 — Push to GitHub

Render deploys from GitHub, so your code has to live there.

```shell
(.venv) $ git status
```

⚠️ **`db.sqlite3` and `staticfiles/` must not appear.** Your database file
contains real password hashes; it does not belong on the internet. If you see
them, your `.gitignore` is wrong — fix it before committing.

```shell
(.venv) $ git add .
(.venv) $ git commit -m "Deploy to Render: production settings, build script"
(.venv) $ git push
```

If this project has no remote yet, create an empty repo at
[github.com/new](https://github.com/new) — public is fine, since the only
secret left in the code is a development key nothing important uses — then:

```shell
(.venv) $ git remote add origin https://github.com/YOUR-NAME/YOUR-REPO.git
(.venv) $ git branch -M main
(.venv) $ git push -u origin main
```

## Step 8 — Create the database on Render

Sign up at [render.com](https://render.com) and choose **Sign in with
GitHub**, so Render can see your repositories later.

Create the database **first** — the web service needs its address the moment
we create it.

**New +** → **Postgres**

| Field | Value |
|-------|-------|
| **Name** | `gringotts-db` |
| **Database / User** | leave the defaults |
| **Region** | the one nearest you, e.g. **Frankfurt (EU Central)** |
| **Instance Type** | **Free** |

Click **Create Database** and wait for the status to become **Available**.

> ⚠️ **Write down your region.** The web service must be in the **same
> region**, or the two cannot reach each other over Render's private network
> and you will spend an evening debugging a timeout. The *Project* field is
> just a folder for tidiness — services share a private network if they are
> in the same workspace and region, whichever project they sit in.

> ⚠️ **Free plans allow only one Postgres per workspace.** If you already
> have one, Render will refuse a second free database.

On the database page, copy the **Internal Database URL**. Two are offered:

- **Internal** — reachable only from inside Render's network. Faster, free,
  never exposed. **This is the one we want.**
- **External** — reachable from anywhere, for connecting from your laptop.

## Step 9 — Create the web service

First, generate a real secret key on your machine:

```shell
(.venv) $ python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Copy the output straight into Render in a moment. Do not put it in a file, do
not commit it, do not paste it into a chat. It should exist in exactly two
places: Render, and your clipboard for ten seconds.

**New +** → **Web Service** → connect your repository.

| Field | Value |
|-------|-------|
| **Name** | `gringotts-bank` → becomes `gringotts-bank.onrender.com` |
| **Language** | Python 3 |
| **Branch** | `main` |
| **Region** | ⚠️ **same region as the database** |
| **Root Directory** | leave empty |
| **Build Command** | `./build.sh` |
| **Start Command** | `gunicorn django_project.wsgi:application` |
| **Instance Type** | **Free** |

> 💡 Service names must be unique across all of Render, so your first choice
> may be taken. Any name works — it only changes your URL.

Then add four **Environment Variables**:

| Key | Value |
|-----|-------|
| `DATABASE_URL` | the **Internal Database URL** you copied |
| `DJANGO_SECRET_KEY` | the key you just generated |
| `DJANGO_ADMIN_PASSWORD` | a strong password you invent now |
| `PYTHON_VERSION` | `3.12.3` |

`PYTHON_VERSION` pins Render to the same Python you developed on. Without it
Render picks its own default.

Notice what you do **not** set: `RENDER` and `RENDER_EXTERNAL_HOSTNAME`.
Render injects both automatically — and those are exactly the two variables
`settings.py` reads to switch into production mode.

Click **Deploy Web Service**.

## Step 10 — Watch the first deploy

Read the log. Every line is something you wrote:

```
==> Running build command './build.sh'...
Collecting django==6.0.7
...
131 static files copied to '/opt/render/project/src/staticfiles', 393 post-processed.
Running migrations:
  Applying accounts.0001_initial... OK
  Applying banking.0001_initial... OK
Created user "alice"
Created superuser "admin"
==> Build successful 🎉
==> Starting service with 'gunicorn django_project.wsgi:application'
[INFO] Booting worker with pid: 52
==> Your service is live 🎉
```

`collectstatic` from `build.sh`, migrations from Lecture 3, users from your
management command, gunicorn from the start command.

Open your URL. **Your bank is on the internet.** Log in as `alice` /
`testpass123` and send Ron money from a real web address.

Then check the three things that prove production settings are working:

1. The page has **styling** → WhiteNoise is serving static files.
2. The address bar shows **https://** with a padlock → the HTTPS settings.
3. Visit a URL that does not exist, like `/nonsense/`. You get a plain
   **"Not Found"**, not Django's yellow debug page → `DEBUG` is off.

Log in at `/admin/` as `admin` with your `DJANGO_ADMIN_PASSWORD`.

---

## When it breaks

| What you see | What it means |
|--------------|---------------|
| `bash: ./build.sh: Permission denied` | Executable bit missing. `git update-index --chmod=+x build.sh`, commit, push. |
| `DisallowedHost at /` | Your hostname is not in `ALLOWED_HOSTS`. Check the `RENDER_EXTERNAL_HOSTNAME` block. |
| Page loads but looks like plain 1995 HTML | Static files. Check the WhiteNoise middleware line is **second**, and that `collectstatic` ran in the build log. |
| `ERR_TOO_MANY_REDIRECTS` | `SECURE_SSL_REDIRECT` without `SECURE_PROXY_SSL_HEADER`. |
| `connection to server ... timeout` | Database and web service in different regions, or you used the External URL. |
| `ValueError: Missing staticfiles manifest entry` | `STORAGES` is outside the `if IS_PRODUCTION:` block. |
| `OperationalError: no password supplied` | `DATABASE_URL` is missing or was pasted incompletely. |
| "Internal Server Error", no detail | That is `DEBUG = False` doing its job. The real traceback is in the Render **Logs** tab, not the browser. |

**The habit to build:** in production, the error is in the **logs**, never in
the browser. Open the Logs tab and read from the bottom up.

### One more trap: two folders with the same name

If you keep a second copy of the project — a lecture copy, a backup, an old
clone — it is very easy to have your **editor** open on one folder while your
**terminal** is in the other. Everything looks correct on screen, and nothing
you type reaches the project you are deploying.

The reliable check is not `manage.py check` — the old settings are also
valid, so it happily reports "no issues". Use git instead:

```shell
(.venv) $ git diff --stat
```

If the file you just edited is not listed, your change is not on disk.

---

## What "free" actually means

Render's free plan is genuinely free and genuinely limited:

- **Your service sleeps.** After 15 minutes with no visitors it spins down,
  and the next visitor waits about a minute for it to wake. Nothing is lost —
  it is just slow. This is why your bank feels broken when you show it to
  someone the next morning.
- **The free database expires after 30 days**, with a 14-day grace period
  before deletion. Your bank will stop working in a month unless you upgrade
  or create a new database. That is not a bug in your code.
- **No shell** — which is why `seed_users` lives in `build.sh`.

And one habit change: **`git push` is now deployment.** Render watches your
`main` branch and rebuilds on every push. Convenient, and slightly
terrifying — from today, pushing a broken migration breaks a live website.
This is the moment your test suite stops being homework and starts being a
safety net.

---

## Recap

- development and production are different worlds; **one settings file, two
  behaviours**, chosen by an environment variable
- secrets live in the **environment**, never in git
- `DEBUG = False` in production, always
- **migrations are why** swapping SQLite for PostgreSQL is a settings change,
  not a rewrite
- static files must be **collected** and served by something real
- `build.sh` runs on every deploy: install → collect → migrate → seed
- rehearse production **locally** first; `manage.py check --deploy` is free advice
- when it breaks in production, the answer is in the **logs**

Happy deploying! 🐍🏦
