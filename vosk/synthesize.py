"""Озвучка текста через vosk-tts."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List

from book_tts.num_utils import normalize_yo_to_e

from .chunker import chunk_text_for_vosk
from .text_utils import replace_numbers_ru


def _normalize_for_vosk(text: str) -> str:
    t = text.strip().replace("*", "")
    t = normalize_yo_to_e(t)
    t = (
        t.replace("«", "")
        .replace("»", "")
        .replace('"', "")
        .replace("„", "")
        .replace("“", "")
        .replace("”", "")
        .replace("‘", "")
        .replace("’", "")
        .replace("—", "-")
        .replace("–", "-")
    )
    return re.sub(r"\s+", " ", t).strip()


def _strip_unsupported_chars_by_model(text: str, model) -> str:
    symbols = model.config.get("phoneme_id_map", {})
    allowed = set(symbols.keys())
    if not allowed:
        return text
    cleaned = "".join(ch for ch in text.lower() if ch in allowed or ch.isspace())
    return re.sub(r"\s+", " ", cleaned).strip()


def _probe_sample_rate(wav_path: Path) -> int:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            str(wav_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(out.stdout.strip())


def _generate_silence_wav(path: Path, duration_sec: float, sample_rate: int) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r={0}:cl=mono".format(sample_rate),
            "-t",
            str(duration_sec),
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def _concat_wavs(parts: List[Path], out_path: Path, tmp_dir: Path, pause_sec: float, sample_rate: int) -> None:
    if not parts:
        raise ValueError("Нет частей для склейки")

    list_file = tmp_dir / "concat_list.txt"
    silence = None
    if pause_sec > 0 and len(parts) > 1:
        silence = tmp_dir / "silence.wav"
        _generate_silence_wav(silence, pause_sec, sample_rate)

    lines: List[str] = []
    for idx, part in enumerate(parts):
        lines.append("file '{0}'\n".format(part.resolve().as_posix()))
        if silence is not None and idx != len(parts) - 1:
            lines.append("file '{0}'\n".format(silence.resolve().as_posix()))

    list_file.write_text("".join(lines), encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out_path)],
        check=True,
        capture_output=True,
    )


def synthesize_txt_to_wav(
    input_path: Path,
    output_path: Path,
    model_name: str = "vosk-model-tts-ru-0.9-multi",
    speaker_id: int = 3,
    max_chars: int = 250,
    pause_sec: float = 0.03,
) -> Path:
    from vosk_tts import Model, Synth

    text = input_path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("Пустой входной txt")
    text = replace_numbers_ru(text)

    # Базовый режим для Vosk: разбиение строго по строкам исходного файла.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    chunks: List[str] = []
    for line in lines:
        if len(line) <= max_chars:
            chunks.append(line)
        else:
            # Фолбэк на случай слишком длинной строки.
            chunks.extend(chunk_text_for_vosk(line, max_chars=max_chars))
    print("Vosk chunks: {0}".format(len(chunks)))

    model = Model(model_name=model_name)
    synth = Synth(model)

    tmp_dir = output_path.parent / ".vosk_tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        parts: List[Path] = []
        for i, chunk in enumerate(chunks, 1):
            part_path = tmp_dir / "part_{0:04d}.wav".format(i)
            prepared = _normalize_for_vosk(chunk)
            if not prepared:
                continue
            try:
                synth.synth(prepared, str(part_path), speaker_id=speaker_id)
            except Exception:
                safe = _strip_unsupported_chars_by_model(prepared, model)
                if not safe:
                    continue
                synth.synth(safe, str(part_path), speaker_id=speaker_id)
            parts.append(part_path)

        if not parts:
            raise ValueError("Нет озвучиваемых чанков")
        sample_rate = _probe_sample_rate(parts[0])
        _concat_wavs(parts, output_path, tmp_dir, pause_sec, sample_rate)
    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)

    return output_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Vosk TTS: TXT -> WAV")
    ap.add_argument("input", help="Входной .txt")
    ap.add_argument("-o", "--output", help="Выходной .wav")
    ap.add_argument("--model", default="vosk-model-tts-ru-0.9-multi", help="Название модели vosk-tts")
    ap.add_argument("--speaker-id", type=int, default=3, help="speaker_id (0..4)")
    ap.add_argument("--max-chars", type=int, default=250, help="Максимальная длина чанка")
    ap.add_argument("--pause-sec", type=float, default=0.03, help="Пауза между чанками")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.exists():
        sys.exit("Нет файла: {0}".format(inp))
    if inp.suffix.lower() != ".txt":
        sys.exit("Ожидается .txt")

    out = Path(args.output) if args.output else inp.with_suffix(".vosk.wav")
    try:
        result = synthesize_txt_to_wav(
            input_path=inp,
            output_path=out,
            model_name=args.model,
            speaker_id=args.speaker_id,
            max_chars=args.max_chars,
            pause_sec=args.pause_sec,
        )
    except Exception as exc:
        sys.exit("Ошибка: {0}".format(exc))

    print("Готово: {0}".format(result))


if __name__ == "__main__":
    main()
