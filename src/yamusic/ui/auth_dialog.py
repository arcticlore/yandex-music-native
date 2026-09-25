"""Authentication dialog: OAuth device flow (browser) or manual token."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from yamusic.api.auth import open_verification_page, start_device_flow
from yamusic.api.service import YandexApi

log = logging.getLogger(__name__)


class AuthDialog(QDialog):
    """Two-tab login: browser confirmation or paste-token."""

    # device-flow callbacks arrive from the worker thread — marshal to GUI
    code_arrived = Signal(object)
    flow_succeeded = Signal(str)
    flow_failed = Signal(str)

    def __init__(self, api: YandexApi, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self._cancelled = False
        self._token_ok = False
        self.setWindowTitle("Вход — Яндекс Музыка")
        self.setMinimumWidth(480)
        self.setModal(True)
        self.code_arrived.connect(self._show_code)
        self.flow_succeeded.connect(self._flow_ok)
        self.flow_failed.connect(self._flow_err)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        intro = QLabel(
            "Выберите способ входа. Для первого запуска проще всего подтвердить "
            "код в браузере — токен сохранится в Secret Service или файл 0600."
        )
        intro.setWordWrap(True)
        intro.setObjectName("Dim")
        layout.addWidget(intro)

        tabs = QTabWidget()
        layout.addWidget(tabs)

        # -- tab 1: device flow
        browser_tab = QWidget()
        b = QVBoxLayout(browser_tab)
        b.setSpacing(10)
        self.code_label = QLabel("Нажмите «Получить код», затем подтвердите вход в браузере.")
        self.code_label.setWordWrap(True)
        self.code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code_label.setStyleSheet("font-size: 18px; font-weight: 700; color: #ffdb4d;")
        b.addWidget(self.code_label)
        self.url_label = QLabel("")
        self.url_label.setWordWrap(True)
        self.url_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.url_label.setObjectName("Dim")
        b.addWidget(self.url_label)
        self.status_label = QLabel("")
        self.status_label.setObjectName("Dim")
        b.addWidget(self.status_label)
        row = QHBoxLayout()
        self.btn_code = QPushButton("Получить код")
        self.btn_code.setObjectName("Accent")
        self.btn_code.clicked.connect(self._start_flow)
        self.btn_cancel_flow = QPushButton("Отмена")
        self.btn_cancel_flow.clicked.connect(self._cancel_flow)
        self.btn_cancel_flow.setEnabled(False)
        row.addWidget(self.btn_code)
        row.addWidget(self.btn_cancel_flow)
        row.addStretch(1)
        b.addLayout(row)
        b.addStretch(1)
        tabs.addTab(browser_tab, "Браузер")

        # -- tab 2: token
        token_tab = QWidget()
        t = QVBoxLayout(token_tab)
        t.setSpacing(10)
        hint = QLabel(
            "Токен можно получить на <b>oauth.yandex.ru</b> через свой OAuth-клиент "
            "или из Developer Tools браузера (заголовок Authorization)."
        )
        hint.setWordWrap(True)
        hint.setObjectName("Dim")
        t.addWidget(hint)
        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText("OAuth-токен Яндекса")
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        t.addWidget(self.token_edit)
        self.token_status = QLabel("")
        self.token_status.setObjectName("Dim")
        t.addWidget(self.token_status)
        btn_token = QPushButton("Войти по токену")
        btn_token.setObjectName("Accent")
        btn_token.clicked.connect(self._login_by_token)
        t.addWidget(btn_token, alignment=Qt.AlignmentFlag.AlignRight)
        t.addStretch(1)
        tabs.addTab(token_tab, "Токен")

        self.buttons = QHBoxLayout()
        self.buttons.addStretch(1)
        btn_quit = QPushButton("Выход")
        btn_quit.clicked.connect(self.reject)
        self.buttons.addWidget(btn_quit)
        layout.addLayout(self.buttons)

    # -- device flow --------------------------------------------------------

    def _start_flow(self) -> None:
        self._cancelled = False
        self.btn_code.setEnabled(False)
        self.btn_cancel_flow.setEnabled(True)
        self.status_label.setText("Запрашиваю код устройства…")
        self.code_label.setText("····-····")

        # NOTE: these thunks run in the API worker thread; they only emit
        # Qt signals, which are delivered queued in the GUI thread.
        def on_code(code: Any) -> None:
            self.code_arrived.emit(code)

        def on_ok(token: str) -> None:
            self.flow_succeeded.emit(token)

        def on_err(message: str) -> None:
            self.flow_failed.emit(message)

        start_device_flow(
            self.api,
            on_code,
            on_ok,
            on_err,
            should_cancel=lambda: self._cancelled,
        )

    def _show_code(self, code: Any) -> None:
        user_code = getattr(code, "user_code", None)
        url = getattr(code, "verification_url", None)
        if user_code:
            self.code_label.setText(str(user_code))
        if url:
            self.url_label.setText(f'<a href="{url}">{url}</a>')
            open_verification_page(str(url))
        self.status_label.setText("Подтвердите вход в браузере…")

    def _flow_ok(self, _token: str) -> None:
        self.status_label.setText("Готово! Закрываю окно…")
        self._token_ok = True
        self.accept()

    def _flow_err(self, message: str) -> None:
        self.btn_code.setEnabled(True)
        self.btn_cancel_flow.setEnabled(False)
        if self._cancelled:
            self.status_label.setText("Отменено")
            return
        self.status_label.setText(f"Ошибка: {message}")

    def _cancel_flow(self) -> None:
        self._cancelled = True
        self.btn_cancel_flow.setEnabled(False)
        self.btn_code.setEnabled(True)
        self.status_label.setText("Отмена…")

    # -- token tab ----------------------------------------------------------

    def _login_by_token(self) -> None:
        token = self.token_edit.text().strip()
        if not token:
            self.token_status.setText("Введите токен")
            return
        self.token_status.setText("Проверяю токен…")

        def _ok(_status: object) -> None:
            self.token_status.setText("Успешно!")
            self._token_ok = True
            self.accept()

        def _err(message: str) -> None:
            self.token_status.setText(message)
            QMessageBox.warning(self, "Ошибка входа", message)

        self.api.validate_token(token, _ok, _err)

    # -----------------------------------------------------------------------

    @property
    def authorized(self) -> bool:
        return self._token_ok
