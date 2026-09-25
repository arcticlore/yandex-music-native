"""Modal login window for yandex-music-linux.

The dialog drives :class:`core.auth.AuthService`, shows an animated spinner
while the browser OAuth flow is running, accepts a pasted token, and renders
the resulting profile (name, avatar, Plus badge) or a readable error.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.auth import AuthService
from core.config_manager import ConfigManager

log = logging.getLogger(__name__)

BG = "#0e0e14"
PANEL = "#17171f"
ELEVATED = "#20202b"
BORDER = "#2a2a38"
FG = "#f2f2f7"
DIM = "#9a9aae"
ACCENT = "#ffdb4d"
ACCENT_HOVER = "#ffe782"
DANGER = "#ff6b6b"
OK = "#5ad19b"

STYLE = f"""
QDialog {{ background: {BG}; }}
QLabel {{ color: {FG}; background: transparent; }}
QLabel#Title {{ font-size: 20px; font-weight: 700; }}
QLabel#Subtitle {{ color: {DIM}; font-size: 13px; }}
QLabel#Status {{ color: {DIM}; font-size: 12px; }}
QLabel#Error {{ color: {DANGER}; font-size: 12px; }}
QLabel#UserName {{ font-size: 17px; font-weight: 700; }}
QLabel#UserLogin {{ color: {DIM}; font-size: 12px; }}
QLabel#Hint {{ color: {DIM}; font-size: 11px; }}
QFrame#Card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#Avatar {{ background: {ELEVATED}; border: 1px solid {BORDER}; border-radius: 36px; }}
QLineEdit {{ background: {ELEVATED}; border: 1px solid {BORDER}; border-radius: 8px;
 padding: 9px 11px; selection-background-color: {ACCENT}; selection-color: #141414; }}
QLineEdit:focus {{ border-color: {ACCENT}; }}
QPushButton {{ background: {ELEVATED}; color: {FG}; border: 1px solid {BORDER};
 border-radius: 8px; padding: 9px 16px; font-weight: 600; }}
QPushButton:hover {{ background: #262633; }}
QPushButton:disabled {{ color: {DIM}; background: {PANEL}; }}
QPushButton#Primary {{ background: {ACCENT}; color: #141414; border: none; font-weight: 700; }}
QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#Primary:disabled {{ background: #3a3a46; color: #6c6c7a; }}
QPushButton#Link {{ background: transparent; border: none; color: {ACCENT};
 padding: 4px; font-size: 12px; text-align: left; }}
QPushButton#Link:hover {{ color: {ACCENT_HOVER}; text-decoration: underline; }}
QCheckBox {{ color: {DIM}; font-size: 12px; spacing: 7px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border-radius: 4px;
 border: 1px solid {BORDER}; background: {ELEVATED}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QStackedWidget {{ background: transparent; }}
"""


class Spinner(QWidget):
    """Lightweight indeterminate progress indicator."""

    def __init__(self, size: int = 18, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._angle = 0
        self._size = size
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._advance)
        self.setVisible(False)

    def start(self) -> None:
        self.setVisible(True)
        self._timer.start()
        self.update()

    def stop(self) -> None:
        self._timer.stop()
        self.setVisible(False)

    def is_spinning(self) -> bool:
        return self._timer.isActive()

    def _advance(self) -> None:
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self.is_spinning():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(ACCENT), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        inset = 3
        rect = self.rect().adjusted(inset, inset, -inset, -inset)
        painter.drawArc(rect, -self._angle * 16, -110 * 16)
        painter.end()


class _AvatarLoader(QObject):
    """Asynchronously fetches an avatar pixmap without blocking the GUI."""

    loaded = Signal(QPixmap)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None

    def load(self, url: str) -> None:
        if not url or self._reply is not None:
            return
        self._reply = self._manager.get(QNetworkRequest(QUrl(url)))
        self._reply.finished.connect(self._on_finished)

    def _on_finished(self) -> None:
        reply = self._reply
        self._reply = None
        if reply is None:
            return
        try:
            if reply.error() == QNetworkReply.NetworkError.NoError:
                pixmap = QPixmap()
                if pixmap.loadFromData(bytes(reply.readAll())):
                    self.loaded.emit(pixmap)
        except Exception as exc:
            log.debug("avatar decode failed: %s", exc)
        finally:
            reply.deleteLater()

    def abort(self) -> None:
        if self._reply is not None:
            self._reply.abort()
            self._reply = None


class AuthDialog(QDialog):
    """Modal window with browser OAuth and manual token entry."""

    authenticated = Signal(dict)

    def __init__(
        self,
        config: ConfigManager | None = None,
        auth: AuthService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Вход — Яндекс Музыка")
        self.setModal(True)
        self.setFixedWidth(440)
        self.setStyleSheet(STYLE)
        self._config = config or (auth.config if auth else ConfigManager())
        self._auth = auth or AuthService(self._config, self)
        self._owns_auth = auth is None
        self._avatar_loader = _AvatarLoader(self)
        self._avatar_loader.loaded.connect(self._apply_avatar)
        self._build_ui()
        self._connect_signals()
        self._fit_height()

    # -- construction ---------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 16)
        root.setSpacing(10)

        logo = QLabel("♪")
        logo.setStyleSheet(f"color: {ACCENT}; font-size: 32px; background: transparent;")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(logo)

        title = QLabel("Яндекс Музыка")
        title.setObjectName("Title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)

        subtitle = QLabel("Войдите, чтобы слушать треки и синхронизировать плейлисты")
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(subtitle)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_login_page())
        self._stack.addWidget(self._build_success_page())
        root.addWidget(self._stack, 1)

        footer = QHBoxLayout()
        self._manual_link = QPushButton("Ввести токен вручную")
        self._manual_link.setObjectName("Link")
        self._manual_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self._manual_link.clicked.connect(self._toggle_manual)
        footer.addWidget(self._manual_link)
        footer.addStretch(1)
        storage = QLabel(f"Токен хранится: {self._storage_label()}")
        storage.setObjectName("Hint")
        footer.addWidget(storage)
        root.addLayout(footer)

    def _build_login_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(10)

        card = QFrame()
        card.setObjectName("Card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(9)

        self._browser_button = QPushButton("Войти через Яндекс ID")
        self._browser_button.setObjectName("Primary")
        self._browser_button.setMinimumHeight(44)
        self._browser_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._browser_button.clicked.connect(self._start_browser_login)
        card_layout.addWidget(self._browser_button)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self._spinner = Spinner(18)
        status_row.addWidget(self._spinner)
        self._status = QLabel("Готов к авторизации")
        self._status.setObjectName("Status")
        self._status.setWordWrap(True)
        status_row.addWidget(self._status, 1)
        card_layout.addLayout(status_row)

        self._code_label = QLabel("")
        self._code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._code_label.setStyleSheet(
            f"color: {ACCENT}; background: {ELEVATED}; border: 1px solid {BORDER};"
            " border-radius: 8px; padding: 8px; font-size: 20px; font-weight: 700;"
            " letter-spacing: 3px;"
        )
        self._code_label.setVisible(False)
        card_layout.addWidget(self._code_label)

        code_buttons = QHBoxLayout()
        self._copy_code_button = QPushButton("Скопировать код")
        self._copy_code_button.setVisible(False)
        self._copy_code_button.clicked.connect(self._copy_user_code)
        self._reopen_button = QPushButton("Открыть страницу")
        self._reopen_button.setVisible(False)
        self._reopen_button.clicked.connect(self._reopen_confirmation)
        code_buttons.addWidget(self._copy_code_button)
        code_buttons.addWidget(self._reopen_button)
        code_buttons.addStretch(1)
        card_layout.addLayout(code_buttons)

        self._cancel_button = QPushButton("Отменить")
        self._cancel_button.setMaximumWidth(120)
        self._cancel_button.setVisible(False)
        self._cancel_button.clicked.connect(self._cancel_login)
        card_layout.addWidget(self._cancel_button, 0, Qt.AlignmentFlag.AlignLeft)

        layout.addWidget(card)

        self._manual_card = QFrame()
        self._manual_card.setObjectName("Card")
        self._manual_card.setVisible(False)
        manual_layout = QVBoxLayout(self._manual_card)
        manual_layout.setContentsMargins(16, 14, 16, 14)
        manual_layout.setSpacing(8)

        manual_title = QLabel("Ручной ввод токена")
        manual_title.setStyleSheet("font-weight: 700; font-size: 14px;")
        manual_layout.addWidget(manual_title)

        manual_hint = QLabel(
            "Вставьте access_token из ответа OAuth или строку Cookie — приложение само извлечёт токен."
        )
        manual_hint.setObjectName("Hint")
        manual_hint.setWordWrap(True)
        manual_layout.addWidget(manual_hint)

        self._token_input = QLineEdit()
        self._token_input.setPlaceholderText("access_token или Cookie")
        self._token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._token_input.returnPressed.connect(self._submit_manual_token)
        manual_layout.addWidget(self._token_input)

        show_row = QHBoxLayout()
        self._show_token = QCheckBox("Показать токен")
        self._show_token.toggled.connect(self._toggle_token_visibility)
        show_row.addWidget(self._show_token)
        show_row.addStretch(1)
        manual_layout.addLayout(show_row)

        self._manual_button = QPushButton("Проверить токен")
        self._manual_button.setObjectName("Primary")
        self._manual_button.setMinimumHeight(40)
        self._manual_button.clicked.connect(self._submit_manual_token)
        manual_layout.addWidget(self._manual_button)

        self._manual_error = QLabel("")
        self._manual_error.setObjectName("Error")
        self._manual_error.setWordWrap(True)
        self._manual_error.setVisible(False)
        manual_layout.addWidget(self._manual_error)

        layout.addWidget(self._manual_card)
        layout.addStretch(1)
        return page

    def _build_success_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(10)

        card = QFrame()
        card.setObjectName("Card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(9)

        self._avatar = QLabel()
        self._avatar.setObjectName("Avatar")
        self._avatar.setFixedSize(60, 60)
        self._avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._avatar.setScaledContents(False)
        self._avatar.setText("♪")
        self._avatar.setStyleSheet(
            f"color: {ACCENT}; font-size: 30px; background: {ELEVATED};"
            f" border: 1px solid {BORDER}; border-radius: 30px;"
        )
        avatar_row = QHBoxLayout()
        avatar_row.addStretch(1)
        avatar_row.addWidget(self._avatar)
        avatar_row.addStretch(1)
        card_layout.addLayout(avatar_row)

        self._user_name = QLabel("")
        self._user_name.setObjectName("UserName")
        self._user_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self._user_name)

        self._user_login = QLabel("")
        self._user_login.setObjectName("UserLogin")
        self._user_login.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._user_login.setWordWrap(True)
        card_layout.addWidget(self._user_login)

        self._plus_badge = QLabel("")
        self._plus_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._plus_badge.setStyleSheet(
            f"color: #141414; background: {ACCENT}; border-radius: 10px;"
            " padding: 5px 12px; font-weight: 700; font-size: 12px;"
        )
        badge_row = QHBoxLayout()
        badge_row.addStretch(1)
        badge_row.addWidget(self._plus_badge)
        badge_row.addStretch(1)
        card_layout.addLayout(badge_row)

        layout.addWidget(card)

        self._warning = QLabel("")
        self._warning.setWordWrap(True)
        self._warning.setStyleSheet(
            f"color: {ACCENT}; background: {PANEL}; border: 1px solid {BORDER};"
            " border-radius: 10px; padding: 8px 10px; font-size: 12px;"
        )
        self._warning.setVisible(False)
        layout.addWidget(self._warning)

        layout.addStretch(1)

        buttons = QHBoxLayout()
        self._logout_button = QPushButton("Выйти")
        self._logout_button.clicked.connect(self._logout)
        buttons.addWidget(self._logout_button)
        buttons.addStretch(1)
        self._done_button = QPushButton("Готово")
        self._done_button.setObjectName("Primary")
        self._done_button.setMinimumWidth(130)
        self._done_button.clicked.connect(self.accept)
        buttons.addWidget(self._done_button)
        layout.addLayout(buttons)
        return page

    def _connect_signals(self) -> None:
        self._auth.status_changed.connect(self._on_status)
        self._auth.auth_error.connect(self._on_error)
        self._auth.auth_success.connect(self._on_success)
        self._auth.plus_warning.connect(self._on_plus_warning)
        self._auth.browser_login_started.connect(self._on_login_started)
        self._auth.browser_login_finished.connect(self._on_login_finished)
        self._auth.device_code_received.connect(self._on_device_code)

    def _storage_label(self) -> str:
        backend = self._config.storage_backend
        if backend == "keyring":
            return "системном хранилище (keyring)"
        return "файле ~/.config/yandex-music-native/config.json (0600)"

    def _fit_height(self) -> None:
        """Keep the window tall enough for the current page, within the screen."""
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        needed = self.sizeHint().height()
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry().height() if screen is not None else 800
        upper = max(560, int(available * 0.92))
        self.setFixedHeight(max(560, min(needed, upper)))

    # -- public API -----------------------------------------------------------

    @property
    def auth(self) -> AuthService:
        return self._auth

    def is_authenticated(self) -> bool:
        return self._auth.is_authenticated

    def start(self) -> int:
        if self._auth.is_authenticated:
            self._on_success(self._auth.user_data)
        else:
            self._auth.restore_session()
        return self.exec()

    # -- slots ----------------------------------------------------------------

    def _toggle_manual(self) -> None:
        visible = self._manual_card.isHidden()
        self._manual_card.setVisible(visible)
        self._manual_link.setText("Скрыть ручной ввод" if visible else "Ввести токен вручную")
        if visible:
            self._token_input.setFocus()
        self._fit_height()

    def _toggle_token_visibility(self, checked: bool) -> None:
        self._token_input.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)

    def _start_browser_login(self) -> None:
        self._manual_error.setVisible(False)
        if not self._auth.login_via_browser():
            self._set_status("Не удалось запустить авторизацию", error=True)

    def _cancel_login(self) -> None:
        self._auth.cancel_browser_login()
        self._set_status("Авторизация отменена")

    def _submit_manual_token(self) -> None:
        token = self._token_input.text().strip()
        if not token:
            self._show_manual_error("Введите токен")
            return
        self._manual_error.setVisible(False)
        if not self._auth.login_with_token(token):
            self._show_manual_error("Авторизация уже выполняется")

    def _on_login_started(self) -> None:
        if not self._manual_card.isHidden():
            self._manual_card.setVisible(False)
            self._manual_link.setText("Ввести токен вручную")
            self._fit_height()
        mode = self._auth.flow_mode()
        text = "Ожидаем подтверждение в браузере…" if mode == "redirect" else "Запрашиваем код подтверждения…"
        self._set_status(text)
        self._set_busy(True)

    def _on_login_finished(self) -> None:
        self._set_busy(False)
        self._hide_code()

    def _on_device_code(self, user_code: str) -> None:
        self._code_label.setText(user_code)
        self._code_label.setVisible(True)
        self._copy_code_button.setVisible(True)
        self._reopen_button.setVisible(True)
        self._set_status("Подтвердите вход в браузере")
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(user_code)

    def _copy_user_code(self) -> None:
        code = self._auth.user_code or self._code_label.text()
        if not code:
            return
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(code)
        self._copy_code_button.setText("Скопировано")

    def _reopen_confirmation(self) -> None:
        url = self._auth.confirmation_url
        if not QDesktopServices.openUrl(QUrl(url)):
            import webbrowser

            webbrowser.open(url)

    def _hide_code(self) -> None:
        self._code_label.setVisible(False)
        self._code_label.clear()
        self._copy_code_button.setVisible(False)
        self._reopen_button.setVisible(False)
        self._copy_code_button.setText("Скопировать код")

    def _on_status(self, text: str) -> None:
        self._set_status(text)

    def _on_error(self, message: str) -> None:
        self._set_busy(False)
        if self._manual_card.isVisible():
            self._show_manual_error(message)
        else:
            self._set_status(message, error=True)

    def _on_success(self, user_data: dict) -> None:
        self._set_busy(False)
        self._spinner.stop()
        self._user_name.setText(user_data.get("display_name") or user_data.get("login") or "Аккаунт")
        self._user_login.setText(user_data.get("login") or "")
        has_plus = bool(user_data.get("has_plus"))
        self._plus_badge.setText("Яндекс Плюс" if has_plus else "Без подписки")
        self._plus_badge.setStyleSheet(
            (
                f"color: #141414; background: {ACCENT};"
                if has_plus
                else f"color: {DIM}; background: {ELEVATED}; border: 1px solid {BORDER};"
            )
            + " border-radius: 10px; padding: 5px 12px; font-weight: 700; font-size: 12px;"
        )
        self._set_status("Вы вошли в аккаунт")
        self._stack.setCurrentIndex(1)
        self._fit_height()
        avatar_url = user_data.get("avatar_url")
        if avatar_url:
            self._avatar_loader.load(avatar_url)
        self.authenticated.emit(dict(user_data))

    def _on_plus_warning(self, message: str) -> None:
        self._warning.setText(message)
        self._warning.setVisible(True)
        self._fit_height()

    def _logout(self) -> None:
        self._auth.logout()
        self._warning.setVisible(False)
        self._token_input.clear()
        self._avatar.clear()
        self._avatar.setText("♪")
        self._stack.setCurrentIndex(0)
        self._set_status("Вы вышли из аккаунта")
        self._fit_height()

    # -- internals ------------------------------------------------------------

    def _apply_avatar(self, pixmap: QPixmap) -> None:
        self._avatar.setText("")
        self._avatar.setPixmap(
            pixmap.scaled(
                self._avatar.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _set_status(self, text: str, error: bool = False) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {DANGER if error else DIM}; font-size: 12px;")
        if error:
            self._spinner.stop()

    def _set_busy(self, busy: bool) -> None:
        if busy:
            self._spinner.start()
        else:
            self._spinner.stop()
        self._browser_button.setEnabled(not busy)
        self._browser_button.setText("Ожидаем браузер…" if busy else "Войти через Яндекс ID")
        self._cancel_button.setVisible(busy)
        self._manual_button.setEnabled(not busy)
        self._token_input.setEnabled(not busy)

    def _show_manual_error(self, message: str) -> None:
        self._manual_error.setText(message)
        self._manual_error.setVisible(True)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._auth.is_busy:
            self._auth.cancel_browser_login()
        self._avatar_loader.abort()
        if self._owns_auth:
            self._auth.shutdown()
        super().closeEvent(event)
