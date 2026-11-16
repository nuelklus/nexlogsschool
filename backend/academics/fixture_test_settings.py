"""Local-only settings for the disposable PostgreSQL fixture demonstration."""

import os
from uuid import uuid4

from django.core.exceptions import ImproperlyConfigured
from django.test.runner import DiscoverRunner

from config.settings import *  # noqa: F403

if IS_PRODUCTION or os.environ.get("NLSCHOOL_DISPOSABLE_FIXTURE_TEST") != "local-only":  # noqa: F405
    raise ImproperlyConfigured("Set NLSCHOOL_DISPOSABLE_FIXTURE_TEST=local-only outside production.")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "postgres",
        "USER": "nlschool_user",
        "PASSWORD": DATABASES["default"]["PASSWORD"],  # noqa: F405
        "HOST": "127.0.0.1",
        "PORT": "5432",
        "OPTIONS": {"sslmode": "disable"},
        "CONN_MAX_AGE": 0,
        "TEST": {
            "NAME": f"test_nlschool_fixture_{uuid4().hex}",
            "MIGRATE": False,
        },
    }
}


class FixtureTestRunner(DiscoverRunner):
    """Create only a new database; never reuse or overwrite another database."""

    def setup_databases(self, **kwargs):
        if self.keepdb or self.parallel not in (0, 1):
            raise ImproperlyConfigured("Fixture tests prohibit --keepdb and parallel databases.")

        from django.db import connections

        db = connections["default"]
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT current_database(), current_user, inet_server_addr()::text, "
                "inet_server_port(), rolsuper, rolcreatedb "
                "FROM pg_roles WHERE rolname = current_user"
            )
            identity = cursor.fetchone()
        if identity != ("postgres", "nlschool_user", "127.0.0.1/32", 5432, False, True):
            raise ImproperlyConfigured(f"Unexpected local PostgreSQL identity: {identity!r}")

        creation = db.creation
        original_create = creation.create_test_db
        created = False

        def create_new_only(verbosity, autoclobber, keepdb=False):
            nonlocal created
            if keepdb:
                raise ImproperlyConfigured("Reusing a test database is forbidden.")
            name = creation._get_test_db_name()
            with creation._nodb_cursor() as cursor:
                cursor.execute(f"CREATE DATABASE {db.ops.quote_name(name)}")
            created = True
            return name

        def create_with_cleanup(*args, **kwargs):
            try:
                return original_create(*args, **kwargs)
            except BaseException:
                if created:
                    db.close()
                    creation._destroy_test_db(creation._get_test_db_name(), self.verbosity)
                raise

        creation._create_test_db = create_new_only
        creation.create_test_db = create_with_cleanup
        return super().setup_databases(**kwargs)


TEST_RUNNER = "academics.fixture_test_settings.FixtureTestRunner"
