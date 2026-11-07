import io
import logging
import math
import struct
import threading
import wave

log = logging.getLogger("assistant.overlay.sounds")

_RATE = 22050

# kind -> [(frequency Hz, milliseconds), ...]; a 0 frequency is a rest.
_PATTERNS = {
    "waiting": [(880, 120), (0, 50), (880, 120), (0, 50), (1175, 220)],
    "done": [(523, 110), (659, 110), (784, 200)],
    "meeting": [(660, 160), (0, 40), (990, 160), (0, 40), (660, 160)],
    "reminder": [(740, 140), (988, 260)],
    "custom": [(698, 130), (932, 130), (1245, 230)],
}

_enabled = True
_played: list[str] = []
_cache: dict[str, bytes] = {}
_lock = threading.Lock()


def _tone(freq: int, ms: int) -> bytes:
    count = int(_RATE * ms / 1000)
    if freq == 0:
        return b"\x00\x00" * count
    fade = max(1, int(_RATE * 0.008))
    frames = bytearray()
    for i in range(count):
        env = min(1.0, i / fade, (count - i) / fade)
        sample = int(9000 * env * math.sin(2 * math.pi * freq * i / _RATE))
        frames += struct.pack("<h", sample)
    return bytes(frames)


def wav_bytes(kind: str) -> bytes:
    pattern = _PATTERNS.get(kind, _PATTERNS["custom"])
    with _lock:
        cached = _cache.get(kind)
    if cached is not None:
        return cached
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_RATE)
        w.writeframes(b"".join(_tone(f, ms) for f, ms in pattern))
    data = buf.getvalue()
    with _lock:
        _cache[kind] = data
    return data


def kinds() -> list[str]:
    return list(_PATTERNS)


def is_enabled() -> bool:
    return _enabled


def set_enabled(value: bool) -> None:
    global _enabled
    _enabled = bool(value)


def played() -> list[str]:
    """Kinds actually sent to the audio device, in order (for tests)."""
    return list(_played)


def play(kind: str) -> bool:
    """Play the sound for a card kind; returns False when sound is off."""
    if not _enabled:
        return False
    _played.append(kind)
    data = wav_bytes(kind)

    def run() -> None:
        try:
            import winsound
            winsound.PlaySound(data, winsound.SND_MEMORY)
        except Exception:
            log.exception("sound playback failed")

    threading.Thread(target=run, name="assistant-sound", daemon=True).start()
    return True


def test_sound() -> bool:
    return play("custom")
