#!/usr/bin/env python3
"""Entry point for yandex-music-linux.

Works when executed as ``python src/main.py`` from a checkout (bootstraps
``src/`` onto ``sys.path``) and when installed as a console script.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _bootstrap_path() -> None:
    """Allow running from a source tree without prior PYTHONPATH setup."""
    src = Path(__file__).resolve().parent
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def main() -> int:
    _bootstrap_path()
    if os.environ.get("YML_LEGACY") == "1":
        from yamusic.app import main as _legacy_main

        return _legacy_main()
    from ui.app import main as _app_main

    return _app_main()


if __name__ == "__main__":
    raise SystemExit(main())
