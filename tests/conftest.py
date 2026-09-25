"""Shared pytest fixtures: offscreen Qt, import paths and env defaults."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("YAMUSIC_AO", "null")
os.environ.setdefault("YAMUSIC_SMOKE", "1")
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
