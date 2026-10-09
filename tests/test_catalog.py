from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))


from core.yandex_service import YandexService
from test_playback_controller import Rig


def test_load_album_emits_ready(app):
    rig = Rig(app)
    try:
        s: YandexService = rig.service
        ev = []
        s.album_ready.connect(lambda d: ev.append(d))
        s.album_failed.connect(lambda m: ev.append(("err", m)))
        assert s.load_album("1")
        rig.settle()
        assert ev and ev[0]
    finally:
        rig.close()


def test_load_artist_emits_ready(app):
    rig = Rig(app)
    try:
        s: YandexService = rig.service
        ev = []
        s.artist_ready.connect(lambda d: ev.append(d))
        s.artist_failed.connect(lambda m: ev.append(("err", m)))
        assert s.load_artist("1")
        rig.settle()
        assert ev and ev[0]
    finally:
        rig.close()
