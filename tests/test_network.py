"""Tests for core.network: IPv4 forcing, connection pool, headers, feedback pool.

The pool, the headers and the retry policy are asserted without touching the
network: a fake session records what the library would have sent.

Run: python -m pytest -q tests/test_network.py
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("YML_AUDIO_AO", "null")

from urllib3.util import connection as urllib3_connection  # noqa: E402

from core import network  # noqa: E402
from core.network import (  # noqa: E402
    API_TIMEOUTS,
    CONNECT_TIMEOUT_S,
    COVER_TIMEOUTS,
    DEFAULT_HEADERS,
    POOL_CONNECTIONS,
    POOL_MAXSIZE,
    READ_TIMEOUT_S,
    RETRY_BACKOFF_FACTOR,
    RETRY_TOTAL,
    YANDEX_USER_AGENT,
    PooledAdapter,
    build_client,
    force_ipv4,
    ipv4_forced,
    network_session,
    reset_session,
    retry_policy,
)


@pytest.fixture(autouse=True)
def fresh_session():
    """Every test starts from a closed session so the pool state is its own."""
    reset_session()
    yield
    reset_session()


def test_ipv4_is_forced() -> None:
    assert force_ipv4() is True
    assert ipv4_forced() is True
    assert urllib3_connection.allowed_gai_family() == socket.AF_INET
    assert socket.getaddrinfo("localhost", 80, socket.AF_INET)  # IPv4 lookup still works


def test_force_ipv4_is_idempotent() -> None:
    assert force_ipv4() is True
    first = urllib3_connection.HAS_IPV6
    assert force_ipv4() is True
    assert urllib3_connection.HAS_IPV6 == first is False


def test_network_session_is_shared_and_prepared() -> None:
    session = network_session()
    assert session is network_session(), "one session per process"
    assert session.headers["User-Agent"] == YANDEX_USER_AGENT
    assert session.headers["X-Yandex-Music-Client"] == DEFAULT_HEADERS["X-Yandex-Music-Client"]
    assert ipv4_forced() is True, "building the session forces IPv4"
    adapter = session.get_adapter("https://api.music.yandex.net")
    assert isinstance(adapter, PooledAdapter)
    assert session.get_adapter("http://api.music.yandex.net") is adapter, "http uses the same pool"
    assert (adapter._pool_connections, adapter._pool_maxsize) == (POOL_CONNECTIONS, POOL_MAXSIZE)
    assert (POOL_CONNECTIONS, POOL_MAXSIZE) == (10, 20)
    assert adapter.max_retries.total == RETRY_TOTAL == 3
    assert adapter.max_retries.backoff_factor == RETRY_BACKOFF_FACTOR == 0.3
    assert set(adapter.max_retries.status_forcelist) == {500, 502, 503, 504}


def test_post_requests_are_never_replayed() -> None:
    policy = retry_policy()
    assert "POST" not in (policy.allowed_methods or set()), "rotor feedback must not be duplicated"
    assert "GET" in policy.allowed_methods
    assert policy.raise_on_status is False


def test_api_timeouts_tolerate_a_slow_link() -> None:
    """A mobile connection needs more than the ten seconds covers get."""
    assert CONNECT_TIMEOUT_S == 5.0
    assert READ_TIMEOUT_S == 25.0
    assert API_TIMEOUTS == (5.0, 25.0)
    assert COVER_TIMEOUTS == (CONNECT_TIMEOUT_S, 10.0), "covers stay snappy"


def test_pooled_request_uses_the_api_timeouts() -> None:
    client = build_client("token-value-000000")
    calls: list[dict] = []

    class FakeResponse:
        status_code = 200
        content = b'{"result": {"ok": true}}'

    class RecordingSession:
        def request(self, *args: object, **kwargs: object) -> FakeResponse:
            calls.append(kwargs)
            return FakeResponse()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(network, "network_session", lambda: RecordingSession())
        client._request._request_wrapper("GET", "https://api.music.yandex.net/settings")
    assert calls[0]["timeout"] == API_TIMEOUTS, "the engine owns the API timeout"


def test_cover_timeouts_come_from_the_network_engine() -> None:
    assert COVER_TIMEOUTS == (5.0, 10.0)
    from core import playback_controller

    assert playback_controller.COVER_TIMEOUTS is COVER_TIMEOUTS
    assert playback_controller._cover_session() is network_session(), "covers share the pool"


def test_client_uses_the_pooled_transport() -> None:
    client = build_client("token-value-000000")
    request = client._request
    assert type(request).__name__ == "_PooledRequest"
    assert hasattr(request, "_handle_error_response"), "the library base class is kept"

    class FakeResponse:
        status_code = 200
        content = b'{"result": {"ok": true}}'

    calls: list[tuple] = []

    class RecordingSession:
        def request(self, *args: object, **kwargs: object) -> FakeResponse:
            calls.append((args, kwargs))
            return FakeResponse()

    session = RecordingSession()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(network, "network_session", lambda: session)
        body = request._request_wrapper("GET", "https://api.music.yandex.net/settings", timeout=5)
    assert body == b'{"result": {"ok": true}}'
    (args, kwargs) = calls[0]
    assert args[0] == "GET"
    assert kwargs["headers"]["User-Agent"] == YANDEX_USER_AGENT
    assert kwargs["timeout"] == 5, "the library keeps ownership of its timeout"


def test_client_transport_translates_transport_errors() -> None:
    from yandex_music.exceptions import NetworkError, TimedOutError

    client = build_client("token-value-000000")
    request = client._request

    class Failing:
        def __init__(self, exc: Exception) -> None:
            self.exc = exc

        def request(self, *args: object, **kwargs: object):
            raise self.exc

    cases = {
        "timeout": (requests.exceptions.ReadTimeout("read timed out"), TimedOutError),
        "connection": (requests.exceptions.ConnectionError("refused"), NetworkError),
    }
    for exc, expected in cases.values():
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(network, "network_session", lambda exc=exc: Failing(exc))
            with pytest.raises(expected):
                request._request_wrapper("GET", "https://api.music.yandex.net/settings", timeout=5)


def test_client_transport_raises_on_401() -> None:
    from yandex_music.exceptions import UnauthorizedError

    client = build_client("token-value-000000")
    request = client._request

    class Unauthorized:
        status_code = 401
        content = b'{"error": "invalid-token"}'

        def request(self, *args: object, **kwargs: object) -> Unauthorized:
            return Unauthorized()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(network, "network_session", lambda: Unauthorized())
        with pytest.raises(UnauthorizedError):
            request._request_wrapper("GET", "https://api.music.yandex.net/settings", timeout=5)


def test_feedback_does_not_block_the_api_worker() -> None:
    from core.yandex_service import FEEDBACK_SKIP, FEEDBACK_TRACK_STARTED, YandexService

    sent: list[str] = []
    started = threading.Event()
    release = threading.Event()

    class SlowClient:
        def rotor_station_feedback(self, station, type_, **kwargs: object) -> bool:
            sent.append(type_)
            started.set()
            release.wait(5)
            return True

    service = YandexService("token-000000000", client_factory=lambda token: SlowClient())
    try:
        service._wave_started = True
        service._batch_id = "b-1"
        track = _track()
        # The worker thread is never started: a serial queue could not deliver this.
        assert service._send_feedback(FEEDBACK_SKIP, track, 12.0) is True
        assert service._send_feedback(FEEDBACK_TRACK_STARTED, track) is True
        assert started.wait(5), "feedback went out on a pool thread"
        assert sent[0] == FEEDBACK_SKIP, "the first event is not delayed by a busy worker"
        assert service.feedback_busy is True
        release.set()
    finally:
        service.shutdown()


def test_token_validation_uses_the_shared_client() -> None:
    """Auth must not fall back to a raw client: it would skip IPv4 and the pool."""
    from unittest import mock

    from core.auth import AuthService
    from core.config_manager import ConfigManager

    fake = mock.Mock()
    fake.init.return_value = fake
    service = AuthService(ConfigManager("test-app-network-auth"))
    with mock.patch("core.network.build_client", return_value=fake) as builder:
        try:
            service.validate_token("token-000000000")
        except RuntimeError:
            pass  # the fake has no profile; only the construction matters
    builder.assert_called_once_with("token-000000000")
    assert fake.init.called


def test_feedback_is_reported_on_the_service_thread(app) -> None:
    from core.yandex_service import FEEDBACK_SKIP, YandexService

    class Client:
        def rotor_station_feedback(self, *args: object, **kwargs: object) -> bool:
            return True

    reported: list[tuple[str, str, int]] = []
    main = threading.get_ident()
    service = YandexService("token-000000000", client_factory=lambda token: Client())
    try:
        service._wave_started = True
        service._batch_id = "b-1"
        service.feedback_sent.connect(
            lambda track, event: reported.append((track, event, threading.get_ident()))
        )
        assert service._send_feedback(FEEDBACK_SKIP, _track(), 7.0) is True
        deadline = time.time() + 5
        while time.time() < deadline and not reported:
            app.processEvents()
            time.sleep(0.01)
    finally:
        service.shutdown()
    assert reported, "the signal was delivered"
    assert reported[0][1] == FEEDBACK_SKIP
    assert reported[0][2] == main, "feedback_sent must not fire on a pool thread"


def test_feedback_survives_a_failing_client() -> None:
    from core.yandex_service import FEEDBACK_SKIP, YandexService

    called = threading.Event()

    class Broken:
        def rotor_station_feedback(self, *args: object, **kwargs: object) -> bool:
            called.set()
            raise TimeoutError("timed out")

    events: list[tuple[str, str]] = []
    service = YandexService("token-000000000", client_factory=lambda token: Broken())
    try:
        service._wave_started = True
        service._batch_id = "b-1"
        service.feedback_sent.connect(lambda track, event: events.append((track, event)))
        assert service._send_feedback(FEEDBACK_SKIP, _track(), 5.0) is True
        assert called.wait(5), "the event was actually sent"
        time.sleep(0.2)
    finally:
        service.shutdown()
    assert events == [], "a failed event reports nothing and breaks nothing"


def test_feedback_stops_with_the_service() -> None:
    from core.yandex_service import FEEDBACK_SKIP, YandexService

    class Client:
        def rotor_station_feedback(self, *args: object, **kwargs: object) -> bool:
            return True

    service = YandexService("token-000000000", client_factory=lambda token: Client())
    service._wave_started = True
    service._batch_id = "b-1"
    assert service._send_feedback(FEEDBACK_SKIP, _track(), 5.0) is True
    service.shutdown()
    assert service.feedback_busy is False
    assert service._send_feedback(FEEDBACK_SKIP, _track(), 5.0) is False, "no work after shutdown"


def _track():
    from core.yandex_service import WaveTrack

    return WaveTrack(id="1:10", track_id="1:10", title="Song", artists=("Artist",), duration_ms=180000)
