#!/usr/bin/env python3
"""Entry point for yandex-music-native.

Works when executed as ``python src/main.py`` from a checkout (bootstraps
``src/`` onto ``sys.path``) and when installed as a console script: the
application is the ``core`` (services, audio, desktop) plus ``ui`` (PySide6
shell) stack started by :func:`ui.app.main`.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap_path() -> None:
    """Allow running from a source tree without prior PYTHONPATH setup."""
    src = Path(__file__).resolve().parent
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def main() -> int:
    _bootstrap_path()
    from ui.app import main as _app_main

    return _app_main()


if __name__ == "__main__":
    raise SystemExit(main())
