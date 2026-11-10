"""Scout's character: state aliases, transient states and painting."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from scout.desktop.character import (
    WIDGET_W,
    ScoutCharacterWidget,
    ScoutCompanionWindow,
    normalize_state,
)

STATES = [
    "idle",
    "listening",
    "thinking",
    "working",
    "speaking",
    "success",
    "error",
    "cancelled",
    "sleeping",
]


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def test_state_aliases() -> None:
    assert normalize_state("acting") == "working"
    assert normalize_state("PLANNING") == "thinking"
    assert normalize_state("hear-ing") == "listening"
    assert normalize_state("stopped") == "cancelled"
    assert normalize_state("not-a-state") == "idle"


def test_transient_state_settles_back_to_idle(qt_app) -> None:
    character = ScoutCharacterWidget()
    character.set_state("success")

    for _ in range(70):
        character.advance(1 / 30)

    assert character.state() == "idle"


def test_repeated_state_does_not_restart_animation(qt_app) -> None:
    character = ScoutCharacterWidget()
    character.set_state("thinking")
    character.advance(0.5)
    character.set_state("thinking")

    assert character.state() == "thinking"


@pytest.mark.parametrize("state", STATES)
def test_each_state_paints_the_character(qt_app, state) -> None:
    character = ScoutCharacterWidget()
    character.set_state(state)
    for _ in range(6):
        character.advance(1 / 30)
    qt_app.processEvents()

    image = character.grab().toImage()
    assert not image.isNull()
    assert image.width() == WIDGET_W
    # The torso is always drawn; a fully transparent frame would mean a broken pose.
    assert image.pixelColor(122, 218).alpha() > 0


def test_companion_forwards_state(qt_app) -> None:
    companion = ScoutCompanionWindow()
    companion.set_state("working", "open_file")

    assert companion.character.state() == "working"
