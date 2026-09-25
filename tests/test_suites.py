"""Pytest entrypoints for the self-contained check suites.

The runners (``yandex_service_test.py``, ``core_audio_test.py``) print every
check and raise on the first failure; this module exposes them as ordinary
pytest tests so CI collects one report. ``auth_test.py`` is collected directly
by pytest through ``python_files`` in ``pyproject.toml``.
"""

from __future__ import annotations


def test_yandex_service() -> None:
    import yandex_service_test

    assert yandex_service_test.main() == 0


def test_core_audio() -> None:
    import core_audio_test

    assert core_audio_test.main() == 0
