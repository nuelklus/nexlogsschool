import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase


BACKEND_DIR = Path(__file__).resolve().parents[1]


class ProductionDatabaseTlsSettingsTests(SimpleTestCase):
    def production_environment(self, **overrides):
        environment = os.environ.copy()
        environment.pop('DJANGO_DB_SSLMODE', None)
        environment.update({
            'DJANGO_ENV': 'production',
            'DJANGO_DEBUG': 'false',
            'DJANGO_SECRET_KEY': 'production-test-secret-' + ('x' * 50),
            'DJANGO_ALLOWED_HOSTS': 'api.school.example',
            'NLSCHOOL_FRONTEND_URL': 'https://app.school.example',
            'CORS_ALLOWED_ORIGINS': 'https://app.school.example',
            'CSRF_TRUSTED_ORIGINS': 'https://app.school.example',
            'DJANGO_DB_NAME': 'nlschool',
            'DJANGO_DB_USER': 'nlschool_user',
            'DJANGO_DB_PASSWORD': 'test-password',
            'DJANGO_DB_HOST': 'db.school.example',
            'DJANGO_MEDIA_ROOT': '/var/lib/nlschool/media',
            'EMAIL_BACKEND': 'django.core.mail.backends.smtp.EmailBackend',
            'EMAIL_HOST': 'smtp.school.example',
            'EMAIL_USE_TLS': 'true',
        })
        environment.update(overrides)
        return environment

    def import_settings(self, **overrides):
        return subprocess.run(
            [
                sys.executable,
                '-c',
                'import config.settings; '
                'print(config.settings.DATABASES["default"]["OPTIONS"]["sslmode"])',
            ],
            cwd=BACKEND_DIR,
            env=self.production_environment(**overrides),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_production_defaults_to_verified_database_tls(self):
        result = self.import_settings()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'verify-full')

    def test_production_rejects_disabled_database_tls(self):
        result = self.import_settings(DJANGO_DB_SSLMODE='disable')

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            'Production requires verified PostgreSQL TLS',
            result.stderr,
        )

    def test_production_accepts_verified_ca_mode(self):
        result = self.import_settings(DJANGO_DB_SSLMODE='verify-ca')

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'verify-ca')
