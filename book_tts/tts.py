"""Озвучка чанков книги через Silero TTS."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

import torch

from .audio_pitch import apply_post_tone_wav
from .parser import parse_text_file


def _load_chunks(input_path: Path) -> Tuple[List[Dict[str, str]], Path]:
    if input_path.suffix.lower() == ".txt":
        json_path, _ = parse_text_file(str(input_path))
    elif input_path.suffix.lower() == ".json":
        json_path = input_path
    else:
        raise ValueError("Поддерживаются только .txt и .json")

    data = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("JSON должен быть массивом чанков")

    chunks: List[Dict[str, str]] = []
    for idx, item in enumerate(data, 1):
        if not isinstance(item, dict):
            raise ValueError("Некорректный чанк #{0}".format(idx))
        chunk_type = str(item.get("type", "")).strip()
        text = str(item.get("text", "")).strip()
        if not chunk_type or not text:
            raise ValueError("Пустой type/text в чанке #{0}".format(idx))
        ssml = item.get("ssml")
        raw_tone = item.get("post_tone") or item.get("pitch_shift")
        post_tone = raw_tone.strip() if isinstance(raw_tone, str) else ""
        chunks.append(
            {
                "type": chunk_type,
                "text": text,
                "ssml": ssml.strip() if isinstance(ssml, str) else "",
                "post_tone": post_tone,
            }
        )
    return chunks, json_path


def _prepare_tts_text(text: str) -> str:
    # Звездочки оставляем: это управляющая разметка для Silero.
    return text.strip()


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
    silence: Path | None = None
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


def synthesize_to_wav(
    input_path: Path,
    output_path: Path,
    model_name: str = "v5_4_ru",
    speaker: str = "xenia",
    sample_rate: int = 48000,
    pause_sec: float = 0.022,
) -> Path:
    chunks, json_path = _load_chunks(input_path)
    print("Чанков: {0} (источник: {1})".format(len(chunks), json_path))
    pt_vals = [(c.get("post_tone") or "").strip() for c in chunks]
    pt_nonempty = [v for v in pt_vals if v]
    if pt_nonempty:
        print("post_tone в чанках: {0}".format(dict(Counter(pt_nonempty))))
    else:
        print("post_tone: нет ни в одном чанке")

    model, _ = torch.hub.load(
        repo_or_dir="snakers4/silero-models",
        model="silero_tts",
        language="ru",
        speaker=model_name,
        trust_repo=True,
    )

    tmp_dir = output_path.parent / ".tts_tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        parts: List[Path] = []
        post_tone_applied = 0
        for idx, chunk in enumerate(chunks, 1):
            text = _prepare_tts_text(chunk["text"])
            part_path = tmp_dir / "part_{0:04d}.wav".format(idx)
            ssml_text = chunk.get("ssml", "").strip()
            if ssml_text:
                model.save_wav(ssml_text=ssml_text, speaker=speaker, sample_rate=sample_rate, audio_path=str(part_path))
            else:
                model.save_wav(text=text, speaker=speaker, sample_rate=sample_rate, audio_path=str(part_path))
            pt = (chunk.get("post_tone") or "").strip()
            if pt:
                shifted = tmp_dir / "part_{0:04d}_pt.wav".format(idx)
                apply_post_tone_wav(part_path, shifted, pt, sample_rate)
                shutil.move(str(shifted), str(part_path))
                post_tone_applied += 1
            parts.append(part_path)

        if post_tone_applied:
            print("post_tone: ffmpeg применён к {0} wav-фрагментам".format(post_tone_applied))

        _concat_wavs(parts, output_path, tmp_dir, pause_sec, sample_rate)
    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)

    return output_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Озвучка .txt/.parsed.json через Silero")
    ap.add_argument("input", help="Входной .txt или .json")
    ap.add_argument("-o", "--output", help="Выходной .wav")
    ap.add_argument("--model", default="v5_5_ru", help="Модель Silero")
    ap.add_argument("--speaker", default="eugene", help="Голос")
    ap.add_argument("--sample-rate", type=int, default=48000, help="Частота дискретизации")
    ap.add_argument("--pause-sec", type=float, default=0.022, help="Пауза между чанками в секундах")
    args = ap.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        sys.exit("Нет файла: {0}".format(input_path))

    output_path = Path(args.output) if args.output else input_path.with_suffix(".wav")
    try:
        out = synthesize_to_wav(
            input_path=input_path,
            output_path=output_path,
            model_name=args.model,
            speaker=args.speaker,
            sample_rate=args.sample_rate,
            pause_sec=args.pause_sec,
        )
    except FileNotFoundError as exc:
        sys.exit("Не найден исполняемый файл: {0}".format(exc))
    except (subprocess.CalledProcessError, ValueError) as exc:
        sys.exit("Ошибка: {0}".format(exc))

    print("Готово: {0}".format(out))


if __name__ == "__main__":
    main()
