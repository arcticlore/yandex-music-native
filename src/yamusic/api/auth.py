"""Authentication helpers: token entry and browser (device-flow) OAuth."""

from __future__ import annotations

import logging
import webbrowser
from typing import Any, Callable

from yamusic.api.service import YandexApi

log = logging.getLogger(__name__)


def open_verification_page(url: str) -> None:
    """Open the Yandex confirmation page in the default browser."""
    try:
        webbrowser.open_new_tab(url)
    except Exception as exc:
        log.warning("cannot open browser: %s", exc)


def start_device_flow(
    api: YandexApi,
    on_code: Callable[[Any], None],
    on_success: Callable[[], None],
    on_error: Callable[[str], None],
    should_cancel: Callable[[], bool],
) -> None:
    """Kick off OAuth Device Flow; ``on_code`` receives ``DeviceCode``."""

    def _relay(code: Any) -> None:
        # Called inside the worker thread; Qt queues it to the GUI thread.
        on_code(code)

    def _ok(_token: str) -> None:
        on_success()

    api.device_auth(_relay, _ok, on_error, should_cancel=should_cancel)
