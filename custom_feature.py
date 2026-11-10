"""Scout Custom Code Feature Hook.

This file is automatically detected and loaded by Scout whenever the assistant is enabled.
You can write your custom code feature here to extend Scout's behavior.

Available Hooks:
1. on_enable():
   Called immediately when `enable` is executed (via `scout enable`, `enable.bat`, or typing `enable`).
   Use this to initialize custom models, connect hardware devices, send webhooks, etc.

2. custom_wake_detector(pcm: bytes) -> bool:
   Called continuously on speech audio buffers (16kHz, 16-bit mono PCM).
   Return True if your custom condition or custom wake phrase is matched to summon Scout immediately.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("scout.custom_feature")


def on_enable() -> None:
    """Executed whenever Scout is enabled.

    Put any custom startup logic here (e.g., lighting effects, hardware cues,
    custom logging, loading machine learning weights, etc.).
    """
    logger.info("Custom code feature enabled!")
    # Example: You can write any custom code here


def custom_wake_detector(pcm: bytes) -> bool:
    """Custom wake/activation detector hook.

    Args:
        pcm: Raw 16kHz, 16-bit mono PCM bytes captured from the microphone.

    Returns:
        bool: True if Scout should wake up and start voice interaction immediately.
    """
    # Example:
    # 1. You can pass `pcm` to your own ML model (e.g., openWakeWord, Porcupine, whisper, etc.)
    # 2. Or check specific acoustic frequency / energy triggers
    # 3. Return True when you want Scout to immediately pop up and listen
    return False


def register() -> None:
    """Optional general registration entry point called on import."""
    pass
