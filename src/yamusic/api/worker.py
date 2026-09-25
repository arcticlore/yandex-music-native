"""Asynchronous bridge between Qt and the ``yandex-music`` library.

``ApiWorker`` owns an asyncio event loop inside a dedicated ``QThread``.
Callers submit coroutine factories; results/errors are marshalled back to the
GUI thread through Qt signals (queued connections), so the UI never blocks.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Coroutine

from PySide6.QtCore import QThread, Signal
from yandex_music import ClientAsync

log = logging.getLogger(__name__)

ClientFactory = Callable[[ClientAsync], Coroutine[Any, Any, Any]]


class ApiWorker(QThread):
    """Runs an asyncio loop; exposes ``succeeded``/``failed`` signals."""

    succeeded = Signal(str, object)  # tag, result
    failed = Signal(str, str)  # tag, error message

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: ClientAsync | None = None
        self._token: str | None = None

    # -- thread lifecycle -------------------------------------------------

    def run(self) -> None:  # noqa: D102
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            if self._token:
                self._client = self._make_client(self._token)
            loop.run_forever()
        finally:
            try:
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                if self._client is not None:
                    close = getattr(self._client._request, "close", None)
                    if callable(close):
                        result = close()
                        if asyncio.iscoroutine(result):
                            loop.run_until_complete(result)
            finally:
                loop.close()
                self._loop = None
                log.debug("api loop stopped")

    def stop(self, timeout_ms: int = 3000) -> None:
        """Stop the event loop and wait for the thread to finish."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if not self.wait(timeout_ms):
            log.warning("api thread did not stop in %d ms", timeout_ms)

    # -- client management (thread-safe) ----------------------------------

    @staticmethod
    def _make_client(token: str | None) -> ClientAsync:
        client = ClientAsync(token=token)
        client.device = (
            "os=Linux; os_version=; manufacturer=PC; model=Yandex Music Native; "
            "clid=; device_id=yandex-music-native; uuid=yandex-music-native"
        )
        return client

    def set_token(self, token: str | None) -> None:
        """Install/replace the OAuth token; recreates ``ClientAsync``."""
        self._token = token
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._reset_client)

    def _reset_client(self) -> None:
        self._client = self._make_client(self._token) if self._token else None

    @property
    def has_token(self) -> bool:
        return bool(self._token)

    # -- submission -------------------------------------------------------

    async def _execute(self, factory: ClientFactory, client: ClientAsync) -> Any:
        return await factory(client)

    def submit(self, factory: ClientFactory, tag: str) -> bool:
        """Schedule ``factory(client)`` on the loop; result → signal by tag."""
        if self._loop is None:
            self.failed.emit(tag, "API worker is not running")
            return False

        loop = self._loop

        def _ensure_and_run() -> asyncio.Future[Any]:
            if self._client is None and not self._token:
                fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
                fut.set_exception(RuntimeError("not authorized"))
                return fut
            if self._client is None:
                self._client = self._make_client(self._token)
            assert self._client is not None
            return asyncio.ensure_future(self._execute(factory, self._client))

        def _schedule() -> None:
            try:
                fut = _ensure_and_run()
            except Exception as exc:  # pragma: no cover — defensive
                self.failed.emit(tag, str(exc))
                return

            def _done(f: "asyncio.Future[Any]") -> None:
                try:
                    if f.cancelled():
                        self.failed.emit(tag, "cancelled")
                        return
                    result = f.result()
                    self.succeeded.emit(tag, result)
                except Exception as exc:  # noqa: BLE001 — surfaced to UI
                    log.debug("api call %s failed: %s", tag, exc)
                    self.failed.emit(tag, str(exc))

            fut.add_done_callback(_done)

        loop.call_soon_threadsafe(_schedule)
        return True


def submit_awaitable(worker: ApiWorker, coro: Awaitable[Any], tag: str) -> bool:
    """Convenience for coroutines that do not need the shared client."""

    async def _factory(_client: ClientAsync) -> Any:
        return await coro

    return worker.submit(_factory, tag)
