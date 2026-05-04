"""Test-only Django settings.

Extends the main settings module and overrides only what is needed
to make pytest fast and fully isolated:

- SQLite instead of NeonDB → no network, no "database being accessed
  by other users" errors on teardown, no cloud DB pollution.
- Disabled password hashers → bcrypt/argon2 are slow; MD5 is instant.
- Silenced logging → keeps test output clean.

pytest.ini points DJANGO_SETTINGS_MODULE at this file so it is ALWAYS
used during pytest runs. The main settings.py is unmodified.
"""
from core.settings import *  # noqa: F401, F403 — intentional wildcard import

BASE_DIR_IMPORT = BASE_DIR  # noqa: F405 — imported from settings via *

# ── Database ───────────────────────────────────────────────────────────────────
# Use SQLite for every test run.
# Reasons:
#   1. NeonDB is a shared cloud DB — Django cannot DROP it during teardown
#      when other connections exist (raises OperationalError).
#   2. SQLite creates and destroys the test DB in milliseconds.
#   3. No network latency → full test suite is ~10x faster.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "test_db.sqlite3",  # noqa: F405
    }
}

# ── Password hashers ───────────────────────────────────────────────────────────
# MD5 is not for production but is fine for tests — eliminates the 200 ms
# bcrypt cost per hashed password, which adds up over many test users.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# ── Logging ────────────────────────────────────────────────────────────────────
# Suppress all logging output during tests so pytest output is readable.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": True,
    "handlers": {
        "null": {"class": "logging.NullHandler"},
    },
    "root": {
        "handlers": ["null"],
        "level": "CRITICAL",
    },
}
