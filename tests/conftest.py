"""Shared pytest fixtures: offscreen Qt, import paths and env defaults."""

from __future__ import annotations

import gc
import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("YML_AUDIO_AO", "null")
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="yml-pytest-config-")
os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp(prefix="yml-pytest-cache-")


@pytest.fixture(scope="session")
def app():
    """One QApplication for the whole session: a second one aborts the process."""
    from PySide6.QtWidgets import QApplication

    instance = QApplication.instance()
    if instance is None:
        instance = QApplication(sys.argv)
    return instance


PINNED_TIMERS: list[object] = []


@pytest.fixture(autouse=True)
def pin_live_timers() -> Iterator[None]:
    """Keep every QObject that still owns a running QTimer alive for the session.

    A QTimer event that Qt already put in the queue is not withdrawn when its
    owner is destroyed, so the next ``processEvents()`` of any later module
    reaches freed memory. Holding a reference to the owner makes that
    impossible; the price is a few objects that a test forgot to close.
    """
    yield
    from PySide6.QtCore import QObject, QTimer

    for obj in gc.get_objects():
        if not isinstance(obj, QObject):
            continue
        try:
            children = obj.children()
        except RuntimeError:
            continue
        if any(isinstance(child, QTimer) and child.isActive() for child in children):
            PINNED_TIMERS.append(obj)


@pytest.fixture(autouse=True)
def qt_sweep(app) -> Iterator[None]:
    """Close top-level widgets while their Python wrappers are still alive.

    Qt deletes a widget asynchronously: if the last Python reference dies
    first, a queued paint or timer event reaches freed memory in the next
    ``processEvents()`` call. Closing and draining here keeps every QObject
    valid for the whole session, whichever module created it.
    """
    from PySide6.QtCore import QCoreApplication, QEvent

    yield
    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.fixture()
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point XDG config/cache at a throwaway directory for one test."""
    config = tmp_path / "config"
    cache = tmp_path / "cache"
    config.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    return config


@pytest.fixture()
def tmp(tmp_path: Path) -> Path:
    """Alias for the legacy runners that spell the builtin as ``tmp``."""
    return tmp_path
