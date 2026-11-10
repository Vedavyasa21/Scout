"""Voice helpers: WAV framing, level detection and transcription."""

from __future__ import annotations

import io
import wave

import pytest

from scout.providers import ModelProvider, ProviderError
from scout.voice import Recording, encode_wav, rms, transcribe_wav


def test_wav_roundtrip_has_expected_format() -> None:
    pcm = (b"\x10\x00\xf0\xff") * 160  # 320 samples
    data = encode_wav(pcm, samplerate=16000)
    with wave.open(io.BytesIO(data), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 16000
        assert handle.readframes(1000) == pcm


def test_rms_distinguishes_silence_from_speech() -> None:
    assert rms(b"\x00\x00" * 480) == 0.0
    loud = b"\x00\x20" * 480  # 8192 amplitude
    assert rms(loud) > 500


class FakeProvider(ModelProvider):
    def __init__(self, text: str = "open notepad") -> None:
        super().__init__(model="fake", api_key="unused")
        self.text = text

    def complete(self, **kwargs):  # pragma: no cover - not used here
        raise NotImplementedError

    def transcribe_audio(self, data: bytes, *, mime_type: str = "audio/wav") -> str:
        assert data.startswith(b"RIFF")
        assert mime_type == "audio/wav"
        return self.text


def test_transcribe_normalises_whitespace() -> None:
    recording = Recording(wav=encode_wav(b"\x00\x00" * 100), duration=0.5)
    assert transcribe_wav(FakeProvider("  open   notepad "), recording) == "open notepad"


def test_empty_transcript_is_an_error() -> None:
    recording = Recording(wav=encode_wav(b"\x00\x00" * 100), duration=0.5)
    with pytest.raises(ProviderError, match="No speech"):
        transcribe_wav(FakeProvider("   "), recording)


def test_custom_wake_and_enable_hooks(tmp_path) -> None:
    from scout.voice import (
        get_custom_enable_hook,
        get_custom_wake_detector,
        load_custom_code_feature,
        register_custom_enable_hook,
        register_custom_wake_detector,
        trigger_custom_enable_hook,
    )

    # Test direct registration
    def my_detector(pcm: bytes) -> bool:
        return len(pcm) > 10

    register_custom_wake_detector(my_detector)
    assert get_custom_wake_detector() is my_detector
    assert get_custom_wake_detector()(b"12345678901") is True
    assert get_custom_wake_detector()(b"123") is False

    hook_called = []

    def my_hook() -> None:
        hook_called.append(True)

    register_custom_enable_hook(my_hook)
    assert get_custom_enable_hook() is my_hook
    trigger_custom_enable_hook()
    assert hook_called == [True]

    # Test loading from a custom file
    custom_file = tmp_path / "custom_test_feature.py"
    custom_file.write_text(
        "def custom_wake_detector(pcm):\n"
        "    return b'TRIGGER' in pcm\n\n"
        "def on_enable():\n"
        "    pass\n"
    )
    loaded = load_custom_code_feature(custom_file)
    assert loaded is True
    assert get_custom_wake_detector()(b"hello TRIGGER world") is True
    assert get_custom_wake_detector()(b"hello world") is False
