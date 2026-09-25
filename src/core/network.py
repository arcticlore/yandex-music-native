"""One pooled, IPv4-first HTTP engine for every request the app makes.

Three problems are solved here, all of them visible as hangs or resets:

* **IPv6 stalls.** ``urllib3`` asks ``getaddrinfo`` for ``AF_UNSPEC`` and walks
  the AAAA records first. On a host with a dead IPv6 route every request pays a
  full connect timeout before the IPv4 address is tried, which is what used to
  look like "the app is frozen". :func:`force_ipv4` narrows the lookup to
  ``AF_INET`` for the whole process.
* **No connection reuse.** ``yandex_music`` calls ``requests.request``, which
  builds a session per call: DNS plus TCP plus TLS for every track. The session
  created here keeps a warm pool (10 connections per host, 20 kept) and rides
  out transient failures with a bounded retry.
* **Wrong identity.** Requests without a client User-Agent are slowed down or
  dropped by the Yandex Music edge, so every request carries the official
  client string and the API's own client header.

The session is a process singleton: the API client, the rotor feedback and the
cover downloader all share it, so they also share its pool and its headers.
"""

from __future__ import annotations

import logging
import socket
import threading
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

YANDEX_USER_AGENT = "YandexMusic/2024.04.1#543 (Linux; x86_64)"
YANDEX_CLIENT_HEADER = "YandexMusicAndroid/24023621"
POOL_CONNECTIONS = 10
POOL_MAXSIZE = 20
RETRY_TOTAL = 3
RETRY_BACKOFF_FACTOR = 0.3
RETRY_STATUSES = (500, 502, 503, 504)
RETRY_METHODS = frozenset({"HEAD", "GET", "PUT", "OPTIONS", "TRACE", "DELETE"})
CONNECT_TIMEOUT_S = 5.0
READ_TIMEOUT_S = 10.0
COVER_TIMEOUTS = (CONNECT_TIMEOUT_S, READ_TIMEOUT_S)
DEFAULT_HEADERS = {
    "User-Agent": YANDEX_USER_AGENT,
    # The header the API itself sends; kept so nothing in the request looks foreign.
    "X-Yandex-Music-Client": YANDEX_CLIENT_HEADER,
}

_session: requests.Session | None = None
_session_lock = threading.Lock()
_request_class: type | None = None


class PooledAdapter(HTTPAdapter):
    """Transport adapter with a sized pool and a bounded retry policy."""

    def __init__(
        self,
        *,
        pool_connections: int = POOL_CONNECTIONS,
        pool_maxsize: int = POOL_MAXSIZE,
        max_retries: Retry | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            pool_connections=pool_connections,
            pool_maxsize=pool_maxsize,
            max_retries=retry_policy() if max_retries is None else max_retries,
            **kwargs,
        )


def retry_policy() -> Retry:
    """Retry transport errors and 5xx answers, never a POST.

    Rotor feedback is a POST: replaying it would report a track twice, so the
    method list is limited to the idempotent verbs.
    """
    return Retry(
        total=RETRY_TOTAL,
        backoff_factor=RETRY_BACKOFF_FACTOR,
        status_forcelist=RETRY_STATUSES,
        allowed_methods=RETRY_METHODS,
        raise_on_status=False,
    )


def force_ipv4() -> bool:
    """Restrict every urllib3 lookup to IPv4; returns whether IPv4 is in use.

    ``urllib3.util.connection.HAS_IPV6`` only feeds ``allowed_gai_family()``,
    so clearing it keeps the library's own code path instead of monkeypatching
    a function out from under it.
    """
    from urllib3.util import connection as urllib3_connection

    previous = getattr(urllib3_connection, "HAS_IPV6", False)
    urllib3_connection.HAS_IPV6 = False
    family = urllib3_connection.allowed_gai_family()
    if family != socket.AF_INET:
        urllib3_connection.HAS_IPV6 = previous
        log.warning("IPv4 could not be forced, dual stack stays enabled")
        return False
    log.debug("urllib3 forced to IPv4 (was HAS_IPV6=%s)", previous)
    return True


def ipv4_forced() -> bool:
    """Whether :func:`force_ipv4` is currently in effect."""
    from urllib3.util import connection as urllib3_connection

    return urllib3_connection.allowed_gai_family() == socket.AF_INET


def network_session() -> requests.Session:
    """The shared session: pooled, retrying, IPv4-only, Yandex headers."""
    global _session
    with _session_lock:
        if _session is None:
            force_ipv4()
            session = requests.Session()
            session.headers.update(DEFAULT_HEADERS)
            adapter = PooledAdapter()
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            _session = session
            log.debug(
                "network session ready: pool=%s/%s retries=%s",
                POOL_CONNECTIONS,
                POOL_MAXSIZE,
                RETRY_TOTAL,
            )
        return _session


def reset_session() -> None:
    """Drop the shared session (tests, and a clean close)."""
    global _session
    with _session_lock:
        session, _session = _session, None
    if session is not None:
        try:
            session.close()
        except Exception as exc:  # noqa: BLE001
            log.debug("session close failed: %s", exc)


def _pooled_request_class() -> type:
    """Build (once) a library request class bound to the shared session.

    ``yandex_music.utils.request.Request`` calls the module-level
    ``requests.request`` and has no session parameter, so the transport is
    replaced instead of configured.
    """
    global _request_class
    if _request_class is not None:
        return _request_class
    from yandex_music.utils.request import Request

    class _PooledRequest(Request):
        def _request_wrapper(self, *args: Any, **kwargs: Any) -> bytes:
            from yandex_music.exceptions import NetworkError, TimedOutError

            kwargs = self._prepare_kwargs(kwargs)
            headers = dict(kwargs.get("headers") or {})
            headers["User-Agent"] = YANDEX_USER_AGENT
            kwargs["headers"] = headers
            try:
                response = network_session().request(*args, **kwargs)
            except requests.Timeout as exc:
                raise TimedOutError from exc
            except requests.RequestException as exc:
                raise NetworkError(exc) from exc
            if 200 <= response.status_code <= 299:
                return response.content
            self._handle_error_response(response.status_code, response.content)
            return None  # type: ignore[return-value]

    _request_class = _PooledRequest
    return _request_class


def pooled_request() -> Any:
    """A ``yandex_music`` request object that uses the shared session."""
    return _pooled_request_class()()


def build_client(token: str | None = None) -> Any:
    """A ``yandex_music.Client`` that speaks through the shared session."""
    from yandex_music import Client

    return Client(token, request=pooled_request())


__all__ = [
    "CONNECT_TIMEOUT_S",
    "COVER_TIMEOUTS",
    "DEFAULT_HEADERS",
    "POOL_CONNECTIONS",
    "POOL_MAXSIZE",
    "READ_TIMEOUT_S",
    "RETRY_TOTAL",
    "YANDEX_USER_AGENT",
    "PooledAdapter",
    "build_client",
    "force_ipv4",
    "ipv4_forced",
    "network_session",
    "pooled_request",
    "reset_session",
    "retry_policy",
]
