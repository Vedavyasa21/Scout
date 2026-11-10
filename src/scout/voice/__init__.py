"""Push-to-talk voice input: record a WAV clip and transcribe it with Gemini."""

from __future__ import annotations

import io
import time
import wave
from array import array
from dataclasses import dataclass

from scout.providers import ModelProvider, ProviderError

SAMPLERATE = 16000
CHANNELS = 1
BLOCK_SECONDS = 0.03
SPEECH_RMS = 500.0  # int16 RMS; normal speech is well above room noise


class VoiceError(Exception):
    """Raised when audio capture is unavailable or fails."""


@dataclass(frozen=True)
class Recording:
    wav: bytes
    duration: float


def encode_wav(pcm: bytes, samplerate: int = SAMPLERATE, channels: int = CHANNELS) -> bytes:
    """Wrap raw 16-bit PCM in a mono WAV container."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(samplerate)
        handle.writeframes(pcm)
    return buffer.getvalue()


def rms(pcm: bytes) -> float:
    """Root-mean-square level of little-endian int16 samples."""
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not samples:
        return 0.0
    total = sum(sample * sample for sample in samples)
    return (total / len(samples)) ** 0.5


def record_wav(
    *,
    max_seconds: float = 15.0,
    silence_seconds: float = 1.2,
    start_timeout: float = 6.0,
    threshold: float = SPEECH_RMS,
    samplerate: int = SAMPLERATE,
    device: int | str | None = None,
) -> Recording | None:
    """Record until the speaker pauses. Returns None when nobody speaks.

    Stops after `max_seconds`, or once `silence_seconds` of quiet follows
    speech. A short pre-roll keeps the first syllable from being clipped.
    """
    try:
        import sounddevice as sd
    except Exception as exc:  # pragma: no cover - optional dependency
        raise VoiceError("Voice input needs sounddevice. Run: pip install sounddevice") from exc

    block = int(samplerate * BLOCK_SECONDS)
    pre_roll: list[bytes] = []
    captured: list[bytes] = []
    speech_started = False
    silence_for = 0.0
    waited = 0.0
    started_at = time.perf_counter()

    try:
        with sd.InputStream(
            samplerate=samplerate,
            channels=CHANNELS,
            dtype="int16",
            blocksize=block,
            device=device,
        ) as stream:
            while True:
                data, _overflowed = stream.read(block)
                chunk = bytes(data)
                elapsed = time.perf_counter() - started_at
                level = rms(chunk)
                loud = level >= threshold

                if not speech_started:
                    pre_roll.append(chunk)
                    pre_roll = pre_roll[-8:]
                    waited += BLOCK_SECONDS
                    if loud:
                        speech_started = True
                        captured.extend(pre_roll)
                        silence_for = 0.0
                    elif waited >= start_timeout:
                        return None
                    continue

                captured.append(chunk)
                silence_for = 0.0 if loud else silence_for + BLOCK_SECONDS
                if silence_for >= silence_seconds or elapsed >= max_seconds:
                    break
    except VoiceError:
        raise
    except Exception as exc:
        raise VoiceError(f"Microphone capture failed: {exc}") from exc

    if not captured:
        return None
    pcm = b"".join(captured)
    duration = len(pcm) / (samplerate * 2)
    if duration < 0.2:
        return None
    return Recording(wav=encode_wav(pcm, samplerate), duration=duration)


def transcribe_wav(provider: ModelProvider, recording: Recording) -> str:
    """Transcribe a recording using the configured provider."""
    try:
        text = provider.transcribe_audio(recording.wav, mime_type="audio/wav")
    except ProviderError:
        raise
    except Exception as exc:  # defensive: never leak audio internals
        raise ProviderError(f"Voice transcription failed: {exc}") from exc
    transcript = " ".join(text.split())
    if not transcript:
        raise ProviderError("No speech was detected in the recording.")
    return transcript


from scout.voice.wake import (  # noqa: E402 - circular import guarded by module init order
    GlobalHotkeyListener,
    WakeWordListener,
    get_custom_enable_hook,
    get_custom_wake_detector,
    load_custom_code_feature,
    register_custom_enable_hook,
    register_custom_wake_detector,
    trigger_custom_enable_hook,
)

__all__ = [
    "BLOCK_SECONDS",
    "CHANNELS",
    "GlobalHotkeyListener",
    "Recording",
    "SAMPLERATE",
    "SPEECH_RMS",
    "VoiceError",
    "WakeWordListener",
    "encode_wav",
    "get_custom_enable_hook",
    "get_custom_wake_detector",
    "load_custom_code_feature",
    "record_wav",
    "register_custom_enable_hook",
    "register_custom_wake_detector",
    "rms",
    "transcribe_wav",
    "trigger_custom_enable_hook",
]
