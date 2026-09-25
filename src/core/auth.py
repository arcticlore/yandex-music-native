"""OAuth authorization and session management for yandex-music-linux.

Two login paths are supported:

* browser flow - the token is obtained through the system browser. Two modes
  are available:

  - ``device`` (default): OAuth Device Flow. The bundled Yandex Music client id
    has no redirect URI registered, so an implicit ``redirect_uri`` request is
    rejected with HTTP 400. Device Flow needs no redirect at all: the app shows
    a short user code and opens ``https://oauth.yandex.ru/device`` in the
    browser, then polls the token endpoint.
  - ``redirect``: a temporary ``http://127.0.0.1:23456/callback`` HTTP server
    runs in a background thread and intercepts the token from the browser
    redirect. This mode requires a Yandex OAuth application whose registered
    callback URL matches the redirect exactly; set it with the
    ``YML_OAUTH_CLIENT_ID`` environment variable or the ``oauth_client_id``
    setting.
* manual flow - a token (or a raw cookie string) is pasted by the user.

Tokens are validated with :class:`yandex_music.Client` and the resulting user
profile is exposed through the ``auth_success`` signal.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices

from core.config_manager import ConfigManager

log = logging.getLogger(__name__)

OAUTH_CLIENT_ID = "23cabbbdc6cd418abb4b39c32c41195d"
OAUTH_BASE = "https://oauth.yandex.ru"
DEVICE_AUTH_URL = f"{OAUTH_BASE}/device"
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 23456
REDIRECT_URI = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}/callback"
BROWSER_TIMEOUT_MS = 300_000
CLIENT_ID_ENV = "YML_OAUTH_CLIENT_ID"
DEFAULT_DEVICE_NAME = "yandex-music-linux"

_PAGE = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<title>{title}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#0e0e14;color:#f2f2f7;font:16px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif}}
.card{{text-align:center;padding:48px 56px;background:#17171f;border:1px solid #272735;
border-radius:16px;max-width:440px}}
h1{{font-size:22px;margin:0 0 12px}}
p{{color:#9a9aae;margin:0}}
.mark{{font-size:40px;margin-bottom:12px;color:#ffdb4d}}
</style></head>
<body><div class="card"><div class="mark">{mark}</div>
<h1>{title}</h1><p>{message}</p></div>{script}</body></html>"""

_FORWARD_SCRIPT = """
<script>
(function(){
  var search = location.search ? location.search.slice(1) : '';
  var hash = location.hash ? location.hash.replace(/^#/, '') : '';
  var payload = search || hash;
  if (!payload) {
    document.body.innerHTML = '<div class="card"><h1>Токен не получен</h1>' +
      '<p>Вернитесь в приложение и попробуйте ввести токен вручную.</p></div>';
    return;
  }
  fetch('/receive?' + payload, {method: 'GET'})
    .then(function(r){ return r.text(); })
    .then(function(){ location.replace('/done'); })
    .catch(function(){ location.replace('/failed'); });
})();
</script>"""


class _CallbackHandler(BaseHTTPRequestHandler):
    """Serves the OAuth landing page and receives the token fragment."""

    server_version = "YandexMusicLinux/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        log.debug("oauth callback: " + fmt, *args)

    def _send(self, body: str, status: int = HTTPStatus.OK) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        params = parse_qs(parts.query)
        if parts.path == "/callback":
            if params.get("error"):
                state = self.server.oauth_state
                state.set_error("access_denied", "Авторизация отклонена")
                self._send(
                    _PAGE.format(
                        mark="&#9888;",
                        title="Авторизация не выполнена",
                        message="Вы отклонили запрос доступа. Окно приложения можно закрыть.",
                        script="",
                    )
                )
                return
            self._send(
                _PAGE.format(
                    mark="&#128247;",
                    title="Завершаем вход…",
                    message="Передаём токен в приложение.",
                    script=_FORWARD_SCRIPT,
                )
            )
            return
        if parts.path in ("/receive", "/token"):
            token = self._extract_token(parts)
            if not token:
                self._send(
                    _PAGE.format(
                        mark="&#9888;",
                        title="Токен не получен",
                        message="Вернитесь в приложение и введите токен вручную.",
                        script="",
                    ),
                    HTTPStatus.BAD_REQUEST,
                )
                return
            self.server.oauth_state.deliver(token)
            self._send(
                _PAGE.format(
                    mark="&#10004;",
                    title="Успешная авторизация!",
                    message="Можете закрыть эту вкладку и вернуться в приложение.",
                    script="",
                )
            )
            return
        if parts.path == "/done":
            self._send(
                _PAGE.format(
                    mark="&#10004;",
                    title="Успешная авторизация!",
                    message="Можете закрыть вкладку.",
                    script="",
                )
            )
            return
        self._send(
            _PAGE.format(
                mark="&#9888;",
                title="Страница не найдена",
                message="Закройте вкладку и вернитесь в приложение.",
                script="",
            ),
            HTTPStatus.NOT_FOUND,
        )

    def _extract_token(self, parts: Any) -> str | None:
        params = parse_qs(parts.query)
        values = params.get("access_token") or []
        if values:
            return values[0]
        fragment = parts.fragment
        if fragment:
            fparams = parse_qs(fragment)
            fvalues = fparams.get("access_token") or []
            if fvalues:
                return fvalues[0]
        return None


class _CallbackState:
    """Shared state between the HTTP handler thread and the Qt service."""

    def __init__(self) -> None:
        self.token_event = threading.Event()
        self.error_event = threading.Event()
        self.token: str | None = None
        self.error: str | None = None

    def reset(self) -> None:
        self.token_event.clear()
        self.error_event.clear()
        self.token = None
        self.error = None

    def deliver(self, token: str) -> None:
        self.token = token
        self.token_event.set()

    def set_error(self, code: str, message: str) -> None:
        self.error = f"{message} ({code})"
        self.error_event.set()

    def wait(self, timeout_s: float) -> tuple[str | None, str | None]:
        deadline = timeout_s
        step = 0.2
        waited = 0.0
        while waited < deadline:
            if self.token_event.is_set():
                return self.token, None
            if self.error_event.is_set():
                return None, self.error or "authorization failed"
            if self.token_event.wait(step):
                return self.token, None
            waited += step
        return None, "timeout"


class AuthService(QObject):
    """OAuth flow, token validation and session state."""

    auth_success = Signal(dict)
    auth_error = Signal(str)
    logged_out = Signal()
    plus_warning = Signal(str)
    status_changed = Signal(str)
    browser_login_started = Signal()
    browser_login_finished = Signal()
    device_code_received = Signal(str)

    def __init__(self, config: ConfigManager | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config or ConfigManager()
        self._state = _CallbackState()
        self._server: ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._busy = False
        self._cancelled = False
        self._flow_id = 0
        self._user_data: dict[str, Any] = {}
        self._token: str | None = None
        self._user_code: str | None = None
        self._confirmation_url: str = DEVICE_AUTH_URL
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(BROWSER_TIMEOUT_MS)
        self._timer.timeout.connect(self._on_browser_timeout)

    # -- public state ---------------------------------------------------------

    @property
    def config(self) -> ConfigManager:
        return self._config

    @property
    def is_authenticated(self) -> bool:
        return bool(self._user_data)

    @property
    def is_busy(self) -> bool:
        return self._busy

    @property
    def user_data(self) -> dict[str, Any]:
        return dict(self._user_data)

    @property
    def token(self) -> str | None:
        return self._token

    @property
    def redirect_uri(self) -> str:
        return REDIRECT_URI

    @property
    def user_code(self) -> str | None:
        return self._user_code

    @property
    def confirmation_url(self) -> str:
        return self._confirmation_url

    def client_id(self) -> str:
        env_value = os.environ.get(CLIENT_ID_ENV, "").strip()
        if env_value:
            return env_value
        stored = str(self._config.get("oauth_client_id", "") or "").strip()
        return stored or OAUTH_CLIENT_ID

    def flow_mode(self) -> str:
        if self.client_id() == OAUTH_CLIENT_ID:
            return "device"
        return "redirect"

    def authorize_url(self, force_confirm: bool = True) -> str:
        query = {
            "response_type": "token",
            "client_id": self.client_id(),
            "redirect_uri": REDIRECT_URI,
            "force_confirm": "true" if force_confirm else "false",
        }
        return f"{OAUTH_BASE}/authorize?{urlencode(query)}"

    # -- browser flow ---------------------------------------------------------

    def login_via_browser(self) -> bool:
        if self._busy:
            return False
        if self.flow_mode() == "redirect":
            return self._start_redirect_flow()
        return self._start_device_flow()

    def _begin_browser_login(self) -> int:
        self._cancelled = False
        self._flow_id += 1
        self._busy = True
        self.browser_login_started.emit()
        return self._flow_id

    def _flow_active(self, flow_id: int) -> bool:
        return self._flow_id == flow_id and not self._cancelled

    def _start_device_flow(self) -> bool:
        flow_id = self._begin_browser_login()
        self._set_status("Запрашиваем код подтверждения…")
        threading.Thread(
            target=self._device_worker, args=(flow_id,), name="oauth-device", daemon=True
        ).start()
        return True

    def _device_worker(self, flow_id: int) -> None:
        from yandex_music import Client
        from yandex_music.exceptions import DeviceAuthError, YandexMusicError

        try:
            client = Client()
            code = client.request_device_code(
                device_name=DEFAULT_DEVICE_NAME,
                client_id=self.client_id(),
            )
        except Exception as exc:
            self._fail_device(flow_id, "Не удалось получить код подтверждения: " + self._readable_error(exc))
            return

        if not self._flow_active(flow_id):
            return

        self._user_code = code.user_code
        self._confirmation_url = code.verification_url or DEVICE_AUTH_URL
        if code.verification_url and "code=" not in code.verification_url:
            self._confirmation_url = f"{code.verification_url}?code={code.user_code}"
        self.device_code_received.emit(code.user_code)
        self._set_status("Подтвердите вход в браузере")
        if not self._open_browser(self._confirmation_url):
            self._fail_device(
                flow_id,
                "Не удалось открыть браузер. Введите код вручную на странице подтверждения.",
            )
            return

        interval = max(2, int(getattr(code, "interval", 5) or 5))
        deadline = time.monotonic() + max(60, int(getattr(code, "expires_in", 600) or 600))
        failures = 0
        while time.monotonic() < deadline:
            if not self._flow_active(flow_id):
                return
            time.sleep(interval)
            if not self._flow_active(flow_id):
                return
            try:
                token = client.poll_device_token(code.device_code, client_id=self.client_id())
            except DeviceAuthError as exc:
                self._fail_device(flow_id, f"Ошибка подтверждения входа: {exc}")
                return
            except YandexMusicError as exc:
                failures += 1
                log.debug("device poll failed (%s): %s", failures, exc)
                if failures >= 3:
                    self._fail_device(flow_id, self._readable_error(exc))
                    return
                continue
            failures = 0
            if token is not None:
                self._set_status("Токен получен, проверяем аккаунт…")
                self._complete_login(token.access_token, flow_id)
                return
        self._fail_device(flow_id, "Время подтверждения истекло. Запросите код заново.")

    def _fail_device(self, flow_id: int, message: str) -> None:
        if not self._flow_active(flow_id):
            return
        self._busy = False
        self._set_status(message)
        self.auth_error.emit(message)
        self.browser_login_finished.emit()

    def _start_redirect_flow(self) -> bool:
        if not self._start_server():
            return False
        flow_id = self._begin_browser_login()
        self._set_status("Открываем браузер…")
        url = self.authorize_url()
        if not self._open_browser(url):
            self._stop_server()
            self._busy = False
            self._fail("Не удалось открыть браузер. Введите токен вручную.")
            return False
        self._timer.start()
        threading.Thread(
            target=self._await_token, args=(flow_id,), name="oauth-callback", daemon=True
        ).start()
        return True

    def cancel_browser_login(self) -> None:
        if not self._busy:
            return
        self._cancelled = True
        self._flow_id += 1
        self._timer.stop()
        self._state.set_error("cancelled", "Авторизация отменена")
        self._stop_server()
        self._busy = False
        self._set_status("Авторизация отменена")
        self.browser_login_finished.emit()

    def _finish_browser(self, flow_id: int) -> None:
        if not self._flow_active(flow_id):
            return
        self._busy = False
        self._stop_server()
        self.browser_login_finished.emit()

    def _start_server(self) -> bool:
        self._state.reset()
        try:
            server = ThreadingHTTPServer((CALLBACK_HOST, CALLBACK_PORT), _CallbackHandler)
        except OSError as exc:
            self._fail(
                f"Порт {CALLBACK_PORT} занят (нужен для OAuth): {exc}. "
                "Закройте другое приложение или введите токен вручную."
            )
            return False
        server.daemon_threads = True
        server.oauth_state = self._state
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.2}, name="oauth-server", daemon=True
        )
        thread.start()
        self._server = server
        self._server_thread = thread
        return True

    def _stop_server(self) -> None:
        server, self._server = self._server, None
        self._server_thread = None
        if server is not None:
            try:
                server.shutdown()
            except Exception as exc:
                log.debug("server shutdown: %s", exc)
            try:
                server.server_close()
            except Exception as exc:
                log.debug("server close: %s", exc)

    def _open_browser(self, url: str) -> bool:
        if QDesktopServices.openUrl(QUrl(url)):
            return True
        try:
            return webbrowser.open(url)
        except Exception as exc:
            log.debug("webbrowser.open failed: %s", exc)
            return False

    def _await_token(self, flow_id: int) -> None:
        token, error = self._state.wait(BROWSER_TIMEOUT_MS / 1000)
        if not self._flow_active(flow_id):
            return
        if not token:
            self._stop_server()
            self._busy = False
            if error == "timeout":
                self._fail("Время ожидания истекло. Попробуйте снова или введите токен вручную.")
            elif error:
                self._fail(error)
            self.browser_login_finished.emit()
            return
        self._timer.stop()
        self._set_status("Токен получен, проверяем аккаунт…")
        self._complete_login(token, flow_id)

    def _on_browser_timeout(self) -> None:
        if self._busy:
            self._state.set_error("timeout", "timeout")
            self._stop_server()
            self._busy = False
            self._fail("Время ожидания истекло. Попробуйте снова или введите токен вручную.")
            self.browser_login_finished.emit()

    # -- manual flow ----------------------------------------------------------

    def login_with_token(self, raw_token: str) -> bool:
        if self._busy:
            return False
        token = self.extract_token(raw_token)
        if not token:
            self._fail("Токен пустой. Вставьте значение поля access_token или Cookie.")
            return False
        self._cancelled = False
        self._flow_id += 1
        flow_id = self._flow_id
        self._busy = True
        self._set_status("Проверяем токен…")
        threading.Thread(
            target=self._complete_login, args=(token, flow_id), name="token-login", daemon=True
        ).start()
        return True

    @staticmethod
    def extract_token(raw: str) -> str | None:
        text = str(raw or "").strip()
        if not text:
            return None
        if text.startswith("{"):
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = {}
            for key in ("access_token", "token", "oauth_token", "yandex_token"):
                value = data.get(key)
                if isinstance(value, str) and value:
                    return value.strip()
        match = re.search(r"(?:access_token|oauth_token|yandex_token)[=\"':\s]+([^\s;\"',}]+)", text)
        if match:
            return match.group(1).strip()
        if "=" in text and " " not in text:
            value = text.split("=", 1)[1].strip()
            if value:
                return value
        if re.fullmatch(r"[A-Za-z0-9._\-]{20,}", text):
            return text
        return None

    # -- validation -----------------------------------------------------------

    def _complete_login(self, token: str, flow_id: int | None = None) -> None:
        if flow_id is not None and not self._flow_active(flow_id):
            return
        try:
            user_data = self.validate_token(token)
        except Exception as exc:
            self._busy = False
            self._stop_server()
            self._fail(self._readable_error(exc))
            self.browser_login_finished.emit()
            return
        if flow_id is not None and not self._flow_active(flow_id):
            return
        self._token = token
        self._user_data = user_data
        try:
            backend = self._config.set_token(token)
            log.info("token stored in %s", backend)
        except Exception as exc:
            self._busy = False
            self._stop_server()
            self._fail(f"Не удалось сохранить токен: {exc}")
            self.browser_login_finished.emit()
            return
        self._busy = False
        self._stop_server()
        self._set_status("Авторизация успешна")
        if not user_data.get("has_plus"):
            self.plus_warning.emit(
                "Активной подписки Яндекс Плюс не найдено: треки без подписки "
                "воспроизводятся только 30 секунд."
            )
        self.auth_success.emit(dict(user_data))
        self.browser_login_finished.emit()

    def validate_token(self, token: str) -> dict[str, Any]:
        from yandex_music import Client

        client = Client(token)
        client.init()
        return self.extract_profile(client)

    @staticmethod
    def extract_profile(client: Any) -> dict[str, Any]:
        """Build the session profile from an initialised ``yandex_music.Client``."""
        status = getattr(client, "me", None)
        if status is None:
            raise RuntimeError("Аккаунт не найден: сервер не вернул данные профиля")
        account = getattr(status, "account", None)
        if account is None:
            raise RuntimeError("Аккаунт не найден: ответ без раздела account")
        plus = getattr(status, "plus", None)
        has_plus = bool(getattr(plus, "has_plus", False))
        login = account.login or ""
        full_name = (
            account.display_name
            or account.full_name
            or " ".join(filter(None, [account.first_name, account.second_name]))
            or login
        )
        uid = account.uid
        return {
            "uid": uid,
            "login": login,
            "display_name": full_name,
            "full_name": account.full_name or full_name,
            "first_name": account.first_name or "",
            "avatar_url": AuthService._avatar_url(account, uid, login),
            "has_plus": has_plus,
            "subscription": "Яндекс Плюс" if has_plus else "Без подписки",
            "client": client,
        }

    @staticmethod
    def _avatar_url(account: Any, uid: Any, login: str) -> str | None:
        data = {}
        to_dict = getattr(account, "to_dict", None)
        if callable(to_dict):
            try:
                data = to_dict()
            except Exception:
                data = {}
        if not isinstance(data, dict):
            data = {}
        for key, value in data.items():
            if "avatar" in key.lower() and isinstance(value, str) and value.startswith("http"):
                return value
        avatar = data.get("avatar")
        if isinstance(avatar, dict):
            for key in ("url", "origUrl", "orig_url", "large"):
                value = avatar.get(key)
                if isinstance(value, str) and value.startswith("http"):
                    return value
        try:
            uid_value = int(uid)
        except (TypeError, ValueError):
            uid_value = 0
        if uid_value > 0:
            return f"https://avatars.yandex.net/get-yapic/{uid_value}/islands-200"
        if login:
            return f"https://avatars.yandex.net/get-yapic/{login}/islands-200"
        return None

    @staticmethod
    def _readable_error(exc: Exception) -> str:
        name = type(exc).__name__
        text = str(exc).strip()
        lowered = f"{name} {text}".lower()
        if "unauthorized" in lowered or "401" in lowered:
            return "Токен отклонён сервером. Проверьте, что он актуален."
        if "forbidden" in lowered or "403" in lowered:
            return "Доступ запрещён: у токена нет прав на Яндекс Музыку."
        if "captcha" in lowered:
            return "Сервер запросил капчу. Пройдите авторизацию в браузере ещё раз."
        if "timeout" in lowered or "timed out" in lowered:
            return "Нет связи с сервером Яндекс Музыки. Проверьте подключение."
        if "connection" in lowered or "network" in lowered:
            return "Не удалось соединиться с Яндекс Музыкой. Проверьте подключение."
        if "token" in lowered and any(word in lowered for word in ("invalid", "incorrect", "bad", "expired")):
            return "Токен недействителен или истёк. Получите новый токен."
        if text:
            return f"Не удалось проверить токен: {text}"
        return f"Ошибка проверки токена ({name})"

    # -- session --------------------------------------------------------------

    def restore_session(self) -> bool:
        token = self._config.get_token()
        if not token:
            return False
        self._busy = True
        self._set_status("Восстанавливаем сессию…")
        threading.Thread(
            target=self._restore_worker, args=(token,), name="restore-session", daemon=True
        ).start()
        return True

    def _restore_worker(self, token: str) -> None:
        try:
            user_data = self.validate_token(token)
        except Exception as exc:
            self._busy = False
            self._token = None
            self._user_data = {}
            self._config.delete_token()
            self._set_status("Сохранённая сессия истекла")
            log.info("stored token rejected: %s", exc)
            self.auth_error.emit(self._readable_error(exc))
            return
        self._token = token
        self._user_data = user_data
        self._busy = False
        self._set_status("Сессия восстановлена")
        if not user_data.get("has_plus"):
            self.plus_warning.emit(
                "Активной подписки Яндекс Плюс не найдено: треки без подписки "
                "воспроизводятся только 30 секунд."
            )
        self.auth_success.emit(dict(user_data))

    def logout(self) -> None:
        self._cancel_timer()
        self._flow_id += 1
        self._stop_server()
        self._busy = False
        self._cancelled = True
        self._token = None
        self._user_data = {}
        try:
            self._config.delete_token()
        except Exception as exc:
            log.warning("token delete failed: %s", exc)
        self._set_status("Вы вышли из аккаунта")
        self.logged_out.emit()

    def _cancel_timer(self) -> None:
        if self._timer.isActive():
            self._timer.stop()

    # -- helpers --------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        self.status_changed.emit(text)

    def _fail(self, message: str) -> None:
        self._set_status(message)
        self.auth_error.emit(message)

    def shutdown(self) -> None:
        self._cancel_timer()
        self._stop_server()
