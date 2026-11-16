"""Local-only fixture settings that build disposable test databases with migrations."""

from .fixture_test_settings import *  # noqa: F403

DATABASES["default"]["TEST"]["MIGRATE"] = True  # noqa: F405
