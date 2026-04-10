"""Постобработка высоты тона WAV через ffmpeg (без rubberband)."""

from __future__ import annotations


import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

# Для единиц «Hz»: сдвиг считаем относительно условного F0 (речь ~150–250 Hz).
DEFAULT_REF_F0_HZ = 200.0

_PITCH_SHIFT_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*(%|hz)\s*$", re.IGNORECASE)


def parse_post_tone_to_factor(spec: str, ref_f0_hz: float = DEFAULT_REF_F0_HZ) -> Optional[float]:
    """
    Возвращает множитель частоты дискретизации для asetrate (< 1 — ниже по тону).
    None, если строка пустая или не распознана.
    Примеры: '-5%', '+3%', '-5hz', '-5 hz'
    """
    if not spec or not str(spec).strip():
        return None
    m = _PITCH_SHIFT_RE.match(str(spec).strip())
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2).lower()
    if unit == "%":
        return 1.0 + value / 100.0
    # hz
    denom = ref_f0_hz
    if denom <= 0:
        return None
    numer = denom + value
    if numer <= 0:
        return None
    return numer / denom


def _atempo_factors(inv: float) -> list[float]:
    """Разложить inv в произведение множителей atempo, каждый в [0.5, 2]."""
    parts: list[float] = []
    x = float(inv)
    while x > 2.0 + 1e-9:
        parts.append(2.0)
        x /= 2.0
    while x < 0.5 - 1e-9:
        parts.append(0.5)
        x /= 0.5
    parts.append(x)
    return parts


def apply_post_tone_wav(
    input_wav: Path,
    output_wav: Path,
    spec: str,
    sample_rate: int,
    ref_f0_hz: float = DEFAULT_REF_F0_HZ,
) -> None:
    """Меняет высоту тона, сохраняя длительность (asetrate + aresample + atempo)."""
    factor = parse_post_tone_to_factor(spec, ref_f0_hz=ref_f0_hz)
    if factor is None:
        raise ValueError("Некорректный post_tone: {0!r}".format(spec))
    if abs(factor - 1.0) < 1e-6:
        shutil.copyfile(input_wav, output_wav)
        return

    inv = 1.0 / factor
    tempo_chain = ",".join("atempo={0:.6f}".format(t) for t in _atempo_factors(inv))
    af = "asetrate={sr}*{fac:.9f},aresample={sr},{tc}".format(
        sr=sample_rate, fac=factor, tc=tempo_chain
    )

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(input_wav),
                "-af",
                af,
                "-c:a",
                "pcm_s16le",
                str(tmp_path),
            ],
            check=True,
            capture_output=True,
        )
        shutil.move(str(tmp_path), str(output_wav))
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
