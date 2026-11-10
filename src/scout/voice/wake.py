"""Wake word, voice activation, and global hotkey listener for Scout.

Features:
- Extensible hook for custom user code features (plug in your own model or classifier).
- Speech energy VAD + keyword detection for "Hey Scout", "Scout", "OK Scout".
- Windows native global hotkey (Ctrl+Alt+S) to summon Scout instantly.
- Seamless wake callback that brings up the overlay and starts voice interaction right away.
- Mobile-assistant style "call and interact" loop.
"""

from __future__ import annotations

import contextlib
import importlib.util
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from scout.providers import ModelProvider
from scout.voice import BLOCK_SECONDS, CHANNELS, SAMPLERATE, SPEECH_RMS, encode_wav, rms

logger = logging.getLogger("scout.voice.wake")

# Global registries for custom user code feature
_CUSTOM_DETECTOR: Callable[[bytes], bool] | None = None
_CUSTOM_ENABLE_HOOK: Callable[[], None] | None = None


def register_custom_wake_detector(detector: Callable[[bytes], bool]) -> None:
    """Register a custom code feature to detect wake words or activation triggers.

    Args:
        detector: A callable accepting raw 16kHz 16-bit mono PCM bytes and returning
                  True if Scout should wake up and interact immediately.
    """
    global _CUSTOM_DETECTOR
    _CUSTOM_DETECTOR = detector


def get_custom_wake_detector() -> Callable[[bytes], bool] | None:
    return _CUSTOM_DETECTOR


def register_custom_enable_hook(hook: Callable[[], None]) -> None:
    """Register a custom callback that executes when Scout is enabled."""
    global _CUSTOM_ENABLE_HOOK
    _CUSTOM_ENABLE_HOOK = hook


def get_custom_enable_hook() -> Callable[[], None] | None:
    return _CUSTOM_ENABLE_HOOK


def trigger_custom_enable_hook() -> None:
    """Trigger the custom user enable hook if registered."""
    if _CUSTOM_ENABLE_HOOK is not None:
        try:
            _CUSTOM_ENABLE_HOOK()
        except Exception as exc:
            logger.warning("Error running custom enable hook: %s", exc)


def load_custom_code_feature(path: str | Path | None = None) -> bool:
    """Auto-discover and load user custom code feature.

    Looks for:
    1. Explicit path parameter or SCOUT_CUSTOM_FEATURE_MODULE env var
    2. ./custom_feature.py in the current working directory or Scout workspace
    3. scout/voice/custom.py

    Hooks supported in the custom module:
    - custom_wake_detector(pcm: bytes) -> bool
    - detect_wake(pcm: bytes) -> bool
    - on_enable() -> None
    - register() -> None
    """
    candidate_paths: list[Path] = []
    if path:
        candidate_paths.append(Path(path))
    env_path = os.environ.get("SCOUT_CUSTOM_FEATURE_MODULE")
    if env_path:
        candidate_paths.append(Path(env_path))

    cwd = Path.cwd()
    candidate_paths.extend(
        [
            cwd / "custom_feature.py",
            cwd / "src" / "custom_feature.py",
            cwd / "src" / "scout" / "voice" / "custom.py",
        ]
    )

    for target in candidate_paths:
        if target.is_file():
            try:
                mod_name = f"scout_user_custom_{int(time.time())}"
                spec = importlib.util.spec_from_file_location(mod_name, str(target))
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[mod_name] = mod
                    spec.loader.exec_module(mod)

                    # Look for detectors
                    detector = getattr(mod, "custom_wake_detector", None) or getattr(
                        mod, "detect_wake", None
                    )
                    if callable(detector):
                        register_custom_wake_detector(detector)
                        logger.info("Registered custom wake detector from %s", target)

                    # Look for enable hook
                    enable_hook = getattr(mod, "on_enable", None) or getattr(
                        mod, "on_custom_enable", None
                    )
                    if callable(enable_hook):
                        register_custom_enable_hook(enable_hook)
                        logger.info("Registered custom enable hook from %s", target)

                    # Look for general register function
                    init_fn = getattr(mod, "register", None)
                    if callable(init_fn):
                        init_fn()

                    return True
            except Exception as exc:
                logger.warning("Failed loading custom feature module from %s: %s", target, exc)

    return False


class GlobalHotkeyListener:
    """Listens for a system-wide global hotkey on Windows (Ctrl+Alt+S by default)."""

    HOTKEY_ID = 4201
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    VK_S = 0x53

    def __init__(self, on_hotkey: Callable[[], None]) -> None:
        self.on_hotkey = on_hotkey
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if os.name != "nt":
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="ScoutHotkeyListener")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        success = user32.RegisterHotKey(
            None,
            self.HOTKEY_ID,
            self.MOD_CONTROL | self.MOD_ALT,
            self.VK_S,
        )
        if not success:
            return

        try:
            msg = wintypes.MSG()
            while not self._stop_event.is_set():
                # PeekMessage to allow non-blocking exit check
                has_msg = user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1)  # PM_REMOVE = 1
                if has_msg:
                    if msg.message == 0x0312 and msg.wParam == self.HOTKEY_ID:  # WM_HOTKEY
                        with contextlib.suppress(Exception):
                            self.on_hotkey()
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                else:
                    time.sleep(0.05)
        finally:
            with contextlib.suppress(Exception):
                user32.UnregisterHotKey(None, self.HOTKEY_ID)


class WakeWordListener:
    """Continuous microphone monitor that detects wake words and triggers activation."""

    DEFAULT_WAKE_WORDS = (
        "scout",
        "hey scout",
        "ok scout",
        "okay scout",
        "hi scout",
        "hello scout",
        "yo scout",
        "wake up scout",
        "scout assistant",
        "hey scott",  # Common phonetic misrecognition of Scout
        "scott",
    )

    def __init__(
        self,
        on_wake: Callable[[], None],
        provider: ModelProvider | None = None,
        *,
        wake_words: tuple[str, ...] = DEFAULT_WAKE_WORDS,
        custom_detector: Callable[[bytes], bool] | None = None,
        threshold: float = SPEECH_RMS,
        local_only: bool = True,
    ) -> None:
        self.on_wake = on_wake
        self.provider = provider
        self.local_only = local_only
        self.wake_words = tuple(w.lower() for w in wake_words)

        # Load custom code feature if not already registered
        if custom_detector is None and get_custom_wake_detector() is None:
            load_custom_code_feature()
        self.custom_detector = custom_detector or get_custom_wake_detector()
        self.threshold = threshold

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._is_listening = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._listen_loop, daemon=True, name="ScoutWakeListener"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def pause(self) -> None:
        self._is_listening = False

    def resume(self) -> None:
        self._is_listening = True

    def _listen_loop(self) -> None:
        try:
            import sounddevice as sd
        except Exception:
            return

        block = int(SAMPLERATE * BLOCK_SECONDS)
        pre_roll: list[bytes] = []
        captured: list[bytes] = []
        speech_active = False
        silence_count = 0
        noise_floor = 120.0
        self._is_listening = True

        try:
            with sd.InputStream(
                samplerate=SAMPLERATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=block,
            ) as stream:
                while not self._stop_event.is_set():
                    if not self._is_listening:
                        time.sleep(0.08)
                        continue

                    data, _ = stream.read(block)
                    chunk = bytes(data)
                    level = rms(chunk)

                    if not speech_active:
                        # Adaptive ambient noise floor tracking
                        noise_floor = 0.96 * noise_floor + 0.04 * level
                        trigger_level = max(100.0, min(self.threshold, noise_floor * 1.5))
                        is_loud = level >= trigger_level

                        pre_roll.append(chunk)
                        pre_roll = pre_roll[-12:]  # ~360ms pre-roll
                        if is_loud:
                            speech_active = True
                            captured = list(pre_roll)
                            silence_count = 0
                        continue

                    # Speech is active
                    captured.append(chunk)
                    trigger_level = max(90.0, noise_floor * 1.3)
                    if level < trigger_level:
                        silence_count += 1
                    else:
                        silence_count = 0

                    duration = len(captured) * BLOCK_SECONDS

                    # When speech pauses for 0.4s or reaches max 3.2s
                    if silence_count >= 13 or duration >= 3.2:
                        pcm = b"".join(captured)
                        captured = []
                        speech_active = False
                        silence_count = 0

                        # Check wake word
                        if duration >= 0.35 and self._matches_wake(pcm):
                            self._trigger_wake()

        except Exception as exc:
            logger.warning("Wake listener exception: %s", exc)

    def _matches_wake(self, pcm: bytes) -> bool:
        """Check custom detector, fast speech recognition, or provider transcription."""
        # 1. Custom code detector hook
        detector = self.custom_detector or get_custom_wake_detector()
        if detector is not None:
            try:
                if detector(pcm):
                    return True
            except Exception:
                pass

        # Privacy: never send ambient audio to a cloud recognizer unless the user
        # explicitly opted in. Hotkey and custom local detectors still work.
        if self.local_only:
            return False

        # 2. Fast SpeechRecognition (cloud)
        try:
            import speech_recognition as sr

            r = sr.Recognizer()
            audio_data = sr.AudioData(pcm, SAMPLERATE, 2)
            text = r.recognize_google(audio_data, language="en-US")
            clean = "".join(c for c in text.lower() if c.isalnum() or c.isspace()).strip()
            if any(wake in clean for wake in self.wake_words):
                logger.info("Wake word matched via fast speech recognition: %s", text)
                return True
        except Exception:
            pass

        # 3. Provider transcription fallback
        if self.provider is not None:
            try:
                wav = encode_wav(pcm, SAMPLERATE)
                text = self.provider.transcribe_audio(wav, mime_type="audio/wav")
                clean = "".join(c for c in text.lower() if c.isalnum() or c.isspace()).strip()
                if any(wake in clean for wake in self.wake_words):
                    logger.info("Wake word matched via provider transcription: %s", text)
                    return True
            except Exception:
                pass

        return False

    def _trigger_wake(self) -> None:
        """Trigger the wake callback and briefly pause detection to avoid echo."""
        self._is_listening = False
        try:
            self.on_wake()
        finally:
            # Resume after brief pause
            threading.Timer(2.0, self.resume).start()
