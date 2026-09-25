"""Tests for config storage, OAuth flow and the login dialog.

Run: python tests/auth_test.py
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.auth import (  # noqa: E402
    CALLBACK_HOST,
    CALLBACK_PORT,
    AuthService,
    _CallbackHandler,
)
from core.config_manager import ConfigManager  # noqa: E402

PASSED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    PASSED.append(name)
    print(f"ok: {name}")


class FakeKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, user: str) -> str | None:
        return self.store.get((service, user))

    def set_password(self, service: str, user: str, value: str) -> None:
        self.store[(service, user)] = value

    def delete_password(self, service: str, user: str) -> None:
        self.store.pop((service, user), None)

    def get_keyring(self) -> object:
        return self


def fake_keyring_module() -> FakeKeyring:
    return FakeKeyring()


def test_config_file_fallback(tmp: Path) -> None:
    with mock.patch.object(ConfigManager, "_keyring_module", return_value=None):
        cfg = ConfigManager("test-app-fallback")
        check("config file path", cfg.config_path.name == "config.json", str(cfg.config_path))
        check("keyring unavailable", cfg.keyring_available() is False)
        check("fallback backend", cfg.storage_backend == "file")
        check("config default volume", cfg.get_volume() == 80)
        cfg.set_volume(42)
        check("config volume clamp", cfg.get_volume() == 42)
        cfg.set_visualizer("wave")
        check("config visualizer", cfg.get_visualizer() == "wave")
        cfg.set_visualizer("bogus")
        check("config visualizer invalid", cfg.get_visualizer() == "spectrum")
        cfg.set_quality("lossless")
        check("config quality", cfg.get_quality() == "lossless")
        backend = cfg.set_token("t0k3n-value-1234567890")
        check("set_token falls back to file", backend == "file", backend)
        mode = stat.S_IMODE(cfg.config_path.stat().st_mode)
        check("config file mode 0600", mode == 0o600, oct(mode))
        data = json.loads(cfg.config_path.read_text())
        check("config file token", data.get("access_token") == "t0k3n-value-1234567890")
        check("config get_token", cfg.get_token() == "t0k3n-value-1234567890")
        check("config has_token", cfg.has_token() is True)
        cfg.update({"last_station": "user:mywave"})
        check("config settings kept", cfg.get("last_station") == "user:mywave")
        check(
            "config token survives update",
            cfg.get_token() == "t0k3n-value-1234567890",
        )
        cfg.delete_token()
        check("config token removed", cfg.get_token() is None)
        check(
            "config file no token after delete",
            "access_token" not in json.loads(cfg.config_path.read_text()),
        )
        dir_mode = stat.S_IMODE(cfg.config_dir.stat().st_mode)
        check("config dir mode 0700", dir_mode == 0o700, oct(dir_mode))


def test_config_keyring(tmp: Path) -> None:
    fake = fake_keyring_module()
    with mock.patch.dict(sys.modules, {"keyring": fake}):
        cfg = ConfigManager("test-app-keyring")
        check("keyring detected", cfg.keyring_available() is True, cfg.storage_backend)
        backend = cfg.set_token("keyring-token-abcdefghij")
        check("keyring used", backend == "keyring", backend)
        check("keyring get", cfg.get_token() == "keyring-token-abcdefghij")
        check(
            "token not in file",
            "access_token" not in json.loads(cfg.config_path.read_text()),
        )
        check(
            "keyring contains token",
            fake.store[("yandex-music-native", "oauth-token")] == "keyring-token-abcdefghij",
        )
        cfg.set_volume(7)
        check("settings with keyring", cfg.get_volume() == 7)
        check("keyring token after setting", cfg.get_token() == "keyring-token-abcdefghij")
        cfg.delete_token()
        check("keyring deleted", cfg.get_token() is None)
        check("keyring store empty", not fake.store)


def test_config_corrupted_json() -> None:
    cfg = ConfigManager("test-app-broken")
    cfg.config_path.write_text("{not json", encoding="utf-8")
    cfg2 = ConfigManager("test-app-broken")
    check("corrupted config recovered", cfg2.get_volume() == 80)
    check("corrupted config backed up", (cfg.config_path.parent / "config.json.broken").exists())


def test_extract_token() -> None:
    raw = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"
    check("extract raw", AuthService.extract_token(raw) == raw)
    check("extract json", AuthService.extract_token('{"access_token":"' + raw + '"}') == raw)
    check(
        "extract cookie",
        AuthService.extract_token("yandex_token=" + raw + "; other=1") == raw,
    )
    check("extract pair", AuthService.extract_token("access_token=" + raw) == raw)
    check("extract empty", AuthService.extract_token("") is None)
    check("extract garbage", AuthService.extract_token("hello world") is None)


def test_callback_server() -> None:
    from core.auth import _CallbackState
    from http.server import ThreadingHTTPServer

    state = _CallbackState()
    server = ThreadingHTTPServer((CALLBACK_HOST, CALLBACK_PORT), _CallbackHandler)
    server.daemon_threads = True
    server.oauth_state = state
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    try:
        base = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}"
        with urllib.request.urlopen(base + "/callback", timeout=5) as resp:
            page = resp.read().decode()
        check("callback html", "Завершаем вход" in page)
        check("callback js forward", "fetch('/receive?'" in page)
        token = "callback-token-xyz987654"
        with urllib.request.urlopen(base + "/receive?access_token=" + token, timeout=5) as resp:
            done = resp.read().decode()
        check("receive success page", "Успешная авторизация" in done)
        got, error = state.wait(2)
        check("callback token delivered", got == token, f"{got} {error}")
        with urllib.request.urlopen(base + "/done", timeout=5) as resp:
            check("done page", "Успешная авторизация" in resp.read().decode())
        try:
            urllib.request.urlopen(base + "/missing", timeout=5)
            check("404 page", False, "expected HTTPError")
        except urllib.error.HTTPError as exc:
            check("404 page", exc.code == 404)
    finally:
        server.shutdown()
        server.server_close()


def _fake_client(token: str) -> object:
    account = mock.Mock()
    account.login = "test@yandex.ru"
    account.display_name = "Тест Тестов"
    account.full_name = "Тест Тестов"
    account.first_name = "Тест"
    account.second_name = None
    account.uid = 1234567890
    account.to_dict.return_value = {
        "login": account.login,
        "avatar": "https://avatars.yandex.net/get-yapic/1234567890/islands-200",
    }
    plus = mock.Mock()
    plus.has_plus = True
    status = mock.Mock()
    status.account = account
    status.plus = plus
    client = mock.Mock()
    client.init.return_value = client
    client.me = status
    client.__class__ = mock.Mock  # type: ignore[assignment]
    return client


def test_validate_token_profile(app: QCoreApplication) -> None:
    svc = AuthService(ConfigManager("test-app-validate"))
    fake_client = _fake_client("valid-token-0000")
    with mock.patch("yandex_music.Client", return_value=fake_client) as client_cls:
        data = svc.validate_token("valid-token-0000")
    check("validate client init called", client_cls.return_value.init.called)
    check("validate login", data["login"] == "test@yandex.ru", str(data))
    check("validate name", data["display_name"] == "Тест Тестов")
    check("validate plus", data["has_plus"] is True)
    check("validate avatar", "avatars.yandex.net" in data["avatar_url"])
    check("validate subscription", data["subscription"] == "Яндекс Плюс")

    no_plus = _fake_client("valid-token-0000")
    no_plus.me.plus.has_plus = False
    with mock.patch("yandex_music.Client", return_value=no_plus):
        data2 = svc.validate_token("valid-token-0000")
    check("validate no plus", data2["has_plus"] is False)
    check("validate no plus label", data2["subscription"] == "Без подписки")

    bad = mock.Mock()
    bad.init.side_effect = RuntimeError("401 Unauthorized")
    with mock.patch("yandex_music.Client", return_value=bad):
        try:
            svc.validate_token("bad-token")
            check("validate bad token", False, "no exception")
        except RuntimeError as exc:
            check("validate bad token", "401" in str(exc))
        check("readable error", "отклонён" in AuthService._readable_error(RuntimeError("401 Unauthorized")))
    check("readable error network", "соединиться" in AuthService._readable_error(OSError("Connection reset")))


def test_login_signals(app: QCoreApplication, tmp: Path) -> None:
    cfg = ConfigManager("test-app-signals")
    svc = AuthService(cfg)
    received: list[dict] = []
    errors: list[str] = []
    plus_warnings: list[str] = []
    logouts: list[bool] = []
    svc.auth_success.connect(received.append)
    svc.auth_error.connect(errors.append)
    svc.plus_warning.connect(plus_warnings.append)
    svc.logged_out.connect(lambda: logouts.append(True))

    fake_client = _fake_client("tok")
    fake_client.me.plus.has_plus = False
    with mock.patch("yandex_music.Client", return_value=fake_client):
        check("login_with_token accepted", svc.login_with_token("tok" + "a" * 30))
        deadline = time.time() + 5
        while time.time() < deadline and not received:
            app.processEvents()
            time.sleep(0.02)
    check("auth_success emitted", len(received) == 1, str(errors))
    check("plus warning emitted", len(plus_warnings) == 1, str(plus_warnings))
    check("token persisted", cfg.get_token() is not None)
    check("is_authenticated", svc.is_authenticated is True)
    check("user_data copy", svc.user_data["login"] == "test@yandex.ru")

    svc.logout()
    app.processEvents()
    check("logged_out emitted", logouts == [True])
    check("token cleared", cfg.get_token() is None)
    check("session cleared", svc.is_authenticated is False)

    with mock.patch("yandex_music.Client", side_effect=RuntimeError("invalid token")):
        svc.login_with_token("b" * 40)
        deadline = time.time() + 5
        while time.time() < deadline and not errors:
            app.processEvents()
            time.sleep(0.02)
    check("auth_error emitted", len(errors) == 1, str(errors))
    check("error message russian", "Ошибка" in errors[0] or "токен" in errors[0], errors[0])
    svc.shutdown()


def test_browser_flow_no_token(app: QCoreApplication) -> None:
    svc = AuthService(ConfigManager("test-app-browser"))
    errors: list[str] = []
    finished: list[bool] = []
    observed: dict[str, object] = {}
    svc.auth_error.connect(errors.append)
    svc.browser_login_finished.connect(lambda: finished.append(True))

    def fake_wait(timeout_s: float) -> tuple[None, str]:
        observed["server"] = svc._server
        observed["busy"] = svc.is_busy
        return None, "timeout"

    with mock.patch("PySide6.QtGui.QDesktopServices.openUrl", return_value=True), \
         mock.patch.object(svc._state, "wait", side_effect=fake_wait), \
         mock.patch.dict(os.environ, {"YML_OAUTH_CLIENT_ID": "custom-client-123"}):
        check("login_via_browser started", svc.login_via_browser() is True)
        deadline = time.time() + 5
        while time.time() < deadline and not finished:
            app.processEvents()
            time.sleep(0.02)
    check("server bound during wait", observed.get("server") is not None, str(observed))
    check("busy during wait", observed.get("busy") is True)
    check("browser timeout error", len(errors) == 1 and "Время" in errors[0], str(errors))
    check("browser flow finished", finished == [True])
    check("server stopped", svc._server is None)
    check("not busy", svc.is_busy is False)
    svc.shutdown()


def test_cancel_browser_login(app: QCoreApplication) -> None:
    svc = AuthService(ConfigManager("test-app-cancel"))
    finished: list[bool] = []
    svc.browser_login_finished.connect(lambda: finished.append(True))
    with mock.patch("PySide6.QtGui.QDesktopServices.openUrl", return_value=True):
        check("cancel flow started", svc.login_via_browser() is True)
        check("cancel flow busy", svc.is_busy is True)
        svc.cancel_browser_login()
        app.processEvents()
    check("cancel finished", finished == [True])
    check("cancel not busy", svc.is_busy is False)
    check("cancel server stopped", svc._server is None)
    svc.shutdown()


def test_browser_flow_delivers_token(app: QCoreApplication) -> None:
    svc = AuthService(ConfigManager("test-app-browser-ok"))
    received: list[dict] = []
    svc.auth_success.connect(received.append)
    fake_client = _fake_client("tok")
    with mock.patch("yandex_music.Client", return_value=fake_client), \
         mock.patch("PySide6.QtGui.QDesktopServices.openUrl", return_value=True), \
         mock.patch.dict(os.environ, {"YML_OAUTH_CLIENT_ID": "custom-client-123"}):
        check("browser login started", svc.login_via_browser() is True)
        base = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}"
        token = "browser-flow-token-123"
        for _ in range(50):
            try:
                urllib.request.urlopen(base + "/receive?access_token=" + token, timeout=2).read()
                break
            except OSError:
                time.sleep(0.1)
        deadline = time.time() + 8
        while time.time() < deadline and not received:
            app.processEvents()
            time.sleep(0.02)
    check("browser auth_success", len(received) == 1)
    check("browser token saved", svc.token == token)
    check("browser not busy", svc.is_busy is False)
    svc.shutdown()


def test_device_flow_cancel_stops_polling(app: QCoreApplication) -> None:
    svc = AuthService(ConfigManager("test-app-device-cancel"))
    finished: list[bool] = []
    errors: list[str] = []
    svc.browser_login_finished.connect(lambda: finished.append(True))
    svc.auth_error.connect(errors.append)

    device_code = mock.Mock()
    device_code.user_code = "555444"
    device_code.verification_url = "https://ya.ru/device"
    device_code.interval = 1
    device_code.expires_in = 300
    fake_client = _fake_client("x")
    fake_client.request_device_code.return_value = device_code
    fake_client.poll_device_token.return_value = None

    with mock.patch("yandex_music.Client", return_value=fake_client), \
         mock.patch.object(svc, "_open_browser", return_value=True):
        svc.login_via_browser()
        deadline = time.time() + 5
        while time.time() < deadline and svc.user_code is None:
            app.processEvents()
            time.sleep(0.02)
        check("cancel: code received", svc.user_code == "555444")
        svc.cancel_browser_login()
        app.processEvents()
        polls_at_cancel = fake_client.poll_device_token.call_count
        check("cancel: finished once", finished == [True], str(finished))
        time.sleep(2.5)
        app.processEvents()
        check(
            "cancel: no extra polls",
            fake_client.poll_device_token.call_count - polls_at_cancel <= 1,
            str(fake_client.poll_device_token.call_count - polls_at_cancel),
        )
    check("cancel: no errors", errors == [], str(errors))
    check("cancel: not busy", svc.is_busy is False)
    svc.shutdown()


def test_authorize_url() -> None:
    with mock.patch.dict(os.environ, {"YML_OAUTH_CLIENT_ID": "custom-client-123"}):
        svc = AuthService(ConfigManager("test-app-url"))
        url = svc.authorize_url()
        check("authorize url base", url.startswith("https://oauth.yandex.ru/authorize?"))
        check("authorize response_type", "response_type=token" in url)
        check(
            "authorize redirect",
            "redirect_uri=http%3A%2F%2F127.0.0.1%3A23456%2Fcallback" in url,
            url,
        )
        check("authorize custom client_id", "client_id=custom-client-123" in url, url)
        check("flow mode redirect", svc.flow_mode() == "redirect")
        svc.shutdown()
    svc_default = AuthService(ConfigManager("test-app-url-default"))
    check("default flow mode device", svc_default.flow_mode() == "device")
    check(
        "default client id",
        svc_default.client_id() == "23cabbbdc6cd418abb4b39c32c41195d",
    )
    check(
        "device url",
        svc_default.confirmation_url == "https://oauth.yandex.ru/device",
    )
    svc_default.shutdown()


def test_device_flow(app: QCoreApplication) -> None:
    svc = AuthService(ConfigManager("test-app-device"))
    codes: list[str] = []
    received: list[dict] = []
    finished: list[bool] = []
    svc.device_code_received.connect(codes.append)
    svc.auth_success.connect(received.append)
    svc.browser_login_finished.connect(lambda: finished.append(True))

    device_code = mock.Mock()
    device_code.user_code = "123456"
    device_code.verification_url = "https://oauth.yandex.ru/device"
    device_code.interval = 1
    device_code.expires_in = 60
    oauth_token = mock.Mock()
    oauth_token.access_token = "device-flow-token-xyz"
    fake_client = _fake_client("device-flow-token-xyz")
    fake_client.request_device_code.return_value = device_code
    fake_client.poll_device_token.side_effect = [None, oauth_token]

    with mock.patch("yandex_music.Client", return_value=fake_client), \
         mock.patch.object(svc, "_open_browser", return_value=True):
        check("device flow started", svc.login_via_browser() is True)
        check("device flow busy", svc.is_busy is True)
        deadline = time.time() + 20
        while time.time() < deadline and not received:
            app.processEvents()
            time.sleep(0.02)
    check("device code emitted", codes == ["123456"], str(codes))
    check("device code stored", svc.user_code == "123456")
    check(
        "confirmation url with code",
        svc.confirmation_url == "https://oauth.yandex.ru/device?code=123456",
        svc.confirmation_url,
    )
    check("device request params", fake_client.request_device_code.call_args.kwargs["device_name"] == "yandex-music-linux")
    check("device polled", fake_client.poll_device_token.call_count == 2)
    check("device auth_success", len(received) == 1, str(received))
    check("device token saved", svc.token == "device-flow-token-xyz")
    settle = time.time() + 5
    while time.time() < settle and not finished:
        app.processEvents()
        time.sleep(0.02)
    check("device finished", finished == [True])
    check("device not busy", svc.is_busy is False)
    check("device no local server", svc._server is None)
    svc.shutdown()


def test_device_flow_error(app: QCoreApplication) -> None:
    from yandex_music.exceptions import DeviceAuthError

    svc = AuthService(ConfigManager("test-app-device-err"))
    errors: list[str] = []
    svc.auth_error.connect(errors.append)
    fake_client = mock.Mock()
    fake_client.request_device_code.side_effect = DeviceAuthError("invalid_client")
    with mock.patch("yandex_music.Client", return_value=fake_client), \
         mock.patch.object(svc, "_open_browser", return_value=True):
        svc.login_via_browser()
        deadline = time.time() + 5
        while time.time() < deadline and not errors:
            app.processEvents()
            time.sleep(0.02)
    check("device request error", len(errors) == 1, str(errors))
    check("device error message", "код" in errors[0].lower() or "ошибка" in errors[0].lower(), errors[0])
    check("device error not busy", svc.is_busy is False)
    svc.shutdown()


def test_dialog(app: QApplication) -> None:
    from ui.dialogs.auth_dialog import AuthDialog, Spinner

    dialog = AuthDialog(config=ConfigManager("test-app-dialog"))
    check("dialog title", "Вход" in dialog.windowTitle())
    check("dialog login page", dialog._stack.currentIndex() == 0)
    check("dialog spinner hidden", dialog._spinner.is_spinning() is False)

    dialog._toggle_manual()
    check("manual shown", dialog._manual_card.isHidden() is False)
    check("manual link text", "Скрыть" in dialog._manual_link.text())
    dialog._toggle_manual()
    check("manual hidden", dialog._manual_card.isHidden() is True)

    dialog._toggle_manual()
    dialog._submit_manual_token()
    check("empty token error", dialog._manual_error.isHidden() is False)
    check("empty token message", "Введите токен" in dialog._manual_error.text())
    dialog._token_input.setText("some-token-value-000")
    dialog._show_token.setChecked(True)
    check("token echo normal", dialog._token_input.echoMode().name == "Normal")
    dialog._show_token.setChecked(False)
    check("token echo hidden", dialog._token_input.echoMode().name == "Password")

    with mock.patch("PySide6.QtGui.QDesktopServices.openUrl", return_value=True), \
         mock.patch.object(AuthService, "validate_token", side_effect=AssertionError("no network")), \
         mock.patch.object(dialog._auth, "_open_browser", return_value=True), \
         mock.patch.dict(os.environ, {"YML_OAUTH_CLIENT_ID": "custom-client-123"}):
        dialog._browser_button.click()
        check("browser click starts flow", dialog._browser_button.isEnabled() is False)
        check("spinner on click", dialog._spinner.is_spinning() is True)
        check("cancel visible", dialog._cancel_button.isHidden() is False)
        dialog._cancel_button.click()
        app.processEvents()
    check("spinner stopped", dialog._spinner.is_spinning() is False)
    check("browser button enabled", dialog._browser_button.isEnabled() is True)
    check("browser button label", "Войти через Яндекс ID" in dialog._browser_button.text())

    with mock.patch.object(AuthService, "login_with_token", return_value=False) as manual:
        dialog._manual_button.click()
        check("manual submit calls service", manual.called)
    check("busy error shown", dialog._manual_error.isHidden() is False)

    spinner = Spinner(20)
    spinner.start()
    check("spinner spinning", spinner.is_spinning() is True)
    spinner.stop()
    check("spinner stopped standalone", spinner.is_spinning() is False)

    dialog._on_device_code("987654")
    check("code label shown", dialog._code_label.isHidden() is False)
    check("code label text", dialog._code_label.text() == "987654")
    check("copy button shown", dialog._copy_code_button.isHidden() is False)
    check("reopen button shown", dialog._reopen_button.isHidden() is False)
    check("code status", "Подтвердите" in dialog._status.text())
    dialog._copy_user_code()
    check("copy button label", dialog._copy_code_button.text() == "Скопировано")
    dialog._on_login_finished()
    check("code hidden after finish", dialog._code_label.isHidden() is True)
    check("copy label reset", dialog._copy_code_button.text() == "Скопировать код")

    dialog._on_success(
        {
            "display_name": "Тест Тестов",
            "login": "test@yandex.ru",
            "has_plus": True,
            "avatar_url": "",
        }
    )
    check("success page", dialog._stack.currentIndex() == 1)
    check("user name shown", dialog._user_name.text() == "Тест Тестов")
    check("user login shown", dialog._user_login.text() == "test@yandex.ru")
    check("plus badge", dialog._plus_badge.text() == "Яндекс Плюс")
    check("success status", "вошли" in dialog._status.text())

    dialog._on_success(
        {"display_name": "Без Плюс", "login": "noplus@yandex.ru", "has_plus": False, "avatar_url": ""}
    )
    check("no plus badge", dialog._plus_badge.text() == "Без подписки")

    dialog._on_plus_warning("нет плюса")
    check("warning shown", dialog._warning.isHidden() is False)
    dialog._on_error("Ошибка входа")
    check("error status", "Ошибка входа" in dialog._status.text())
    dialog._logout()
    check("logout back to login", dialog._stack.currentIndex() == 0)
    check("logout status", "вышли" in dialog._status.text())
    dialog._auth.shutdown()
    dialog.close()


def test_dialog_layout(app: QApplication) -> None:
    from PySide6.QtCore import QEventLoop
    from PySide6.QtWidgets import QWidget

    from ui.dialogs.auth_dialog import AuthDialog

    dialog = AuthDialog(config=ConfigManager("test-app-layout"))
    dialog.show()

    def settle() -> None:
        for _ in range(4):
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)

    def audit(page_name: str) -> list[str]:
        page = dialog._stack.currentWidget()
        found: list[str] = []
        for widget in page.findChildren(QWidget):
            if not widget.isVisibleTo(page):
                continue
            rect = widget.geometry()
            if rect.width() < 2 or rect.height() < 2:
                found.append(f"{page_name}: {widget.__class__.__name__} has no size")
            if rect.left() < 0 or rect.top() < 0:
                found.append(f"{page_name}: {widget.__class__.__name__} placed outside page")
            if rect.right() > page.width() or rect.bottom() > page.height():
                found.append(
                    f"{page_name}: {widget.__class__.__name__} clipped "
                    f"({rect.width()}x{rect.height()} in {page.width()}x{page.height()})"
                )
        return found

    problems: list[str] = []
    settle()
    check("layout login height", dialog.height() >= 560, str(dialog.height()))
    problems += audit("login")
    dialog._toggle_manual()
    settle()
    problems += audit("login+manual")
    dialog._on_login_started()
    dialog._on_device_code("76jfbo44")
    settle()
    problems += audit("login+code")
    check("manual hidden on browser start", dialog._manual_card.isHidden() is True)
    check("code label fits", dialog._code_label.height() > 20, str(dialog._code_label.height()))
    dialog._on_success(
        {"display_name": "Иван Петров", "login": "ivan@yandex.ru", "has_plus": True, "avatar_url": ""}
    )
    settle()
    problems += audit("success")
    check("user label has height", dialog._user_name.height() >= 20, str(dialog._user_name.height()))
    check("plus badge has height", dialog._plus_badge.height() >= 20, str(dialog._plus_badge.height()))
    dialog._on_plus_warning(
        "Активной подписки Яндекс Плюс не найдено: треки без подписки "
        "воспроизводятся только 30 секунд."
    )
    settle()
    problems += audit("success+warning")
    check("warning not clipped", dialog._warning.height() >= dialog._warning.heightForWidth(dialog._warning.width()))
    dialog._logout()
    settle()
    problems += audit("logout")
    check("no layout problems", not problems, "; ".join(problems))
    dialog._auth.shutdown()
    dialog.close()


def main() -> int:
    os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="yml-test-config-")
    app = QApplication.instance() or QApplication(sys.argv)
    with tempfile.TemporaryDirectory(prefix="yml-test-cfg-") as raw:
        base = Path(raw)
        os.environ["XDG_CONFIG_HOME"] = str(base / "xdg")
        (base / "xdg").mkdir(parents=True, exist_ok=True)
        test_config_file_fallback(base)
        test_config_keyring(base)
        test_config_corrupted_json()
        test_extract_token()
        test_callback_server()
        test_validate_token_profile(app)
        test_login_signals(app, base)
        test_device_flow(app)
        test_device_flow_error(app)
        test_device_flow_cancel_stops_polling(app)
        test_browser_flow_no_token(app)
        test_cancel_browser_login(app)
        test_browser_flow_delivers_token(app)
        test_authorize_url()
        test_dialog(app)
        test_dialog_layout(app)
    print(f"\nAll {len(PASSED)} auth/config checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
