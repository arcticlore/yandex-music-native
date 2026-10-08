from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))


from ui.widgets.track_context_menu import TrackContextMenu
from test_playback_controller import Rig, make_track
def test_context_menu_has_actions(app):
    rig = Rig(app)
    try:
        menu = TrackContextMenu(rig.controller)
        trk = make_track(1)
        menu.set_track(trk)
        actions = [a.text() for a in menu.actions()]
        assert "Воспроизвести" in actions
        assert "Нравится" in actions
        assert "Скопировать ссылку" in actions
    finally:
        rig.close()
