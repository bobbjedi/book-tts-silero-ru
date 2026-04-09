#!/usr/bin/env python3
"""TXT → WAV через Silero: чанки ~900 символов, пауза между чанками (ffmpeg), опционально *слово* перед ?."""

import argparse
import re
import subprocess
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from focus_questions import wrap_each_word_before_question  # noqa: E402


def split_text_to_chunks(text: str, max_len: int) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    chunks: list[str] = []

    def split_para(p: str) -> list[str]:
        p = " ".join([ln.strip() for ln in p.splitlines() if ln.strip()])
        sentences = re.split(r"(?<=[.!?…])\s+", p)
        out: list[str] = []
        buf = ""
        for s in sentences:
            if not s:
                continue
            if len(buf) + len(s) + 1 > max_len:
                if buf:
                    out.append(buf.strip())
                    buf = ""
            buf = (buf + " " + s).strip() if buf else s
        if buf:
            out.append(buf.strip())
        final: list[str] = []
        for c in out:
            if len(c) <= max_len:
                final.append(c)
            else:
                for i in range(0, len(c), max_len):
                    final.append(c[i : i + max_len])
        return final

    for p in paragraphs:
        chunks.extend(split_para(p))
    return chunks


def generate_silence_wav(path: Path, duration_ms: int, sample_rate: int) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r={sample_rate}:cl=mono",
            "-t",
            str(duration_ms / 1000),
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def concat_wavs(
    parts: list[Path],
    out_path: Path,
    tmp_dir: Path,
    pause_ms: int,
    sample_rate: int,
) -> None:
    list_file = tmp_dir / f"list_{out_path.stem}.txt"
    silence: Path | None = None
    if pause_ms > 0 and len(parts) > 1:
        silence = tmp_dir / f"silence_{pause_ms}ms.wav"
        generate_silence_wav(silence, pause_ms, sample_rate)

    lines: list[str] = []
    for idx, p in enumerate(parts):
        lines.append(f"file '{p.resolve().as_posix()}'\n")
        if silence is not None and idx != len(parts) - 1:
            lines.append(f"file '{silence.resolve().as_posix()}'\n")

    list_file.write_text("".join(lines), encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out_path)],
        check=True,
        capture_output=True,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Silero TXT→WAV, plain text")
    ap.add_argument("input", help="Входной .txt")
    ap.add_argument("-o", "--output", help="Выходной .wav")
    ap.add_argument("--model", default="v5_4_ru", help="Модель Silero")
    ap.add_argument("--speaker", default="aidar", help="Голос")
    ap.add_argument("--sample-rate", type=int, default=48000)
    ap.add_argument("--max-chunk", type=int, default=900)
    ap.add_argument(
        "--plain",
        action="store_true",
        help="Не ставить * вокруг слов перед ?",
    )
    ap.add_argument(
        "--pause-ms",
        type=int,
        default=200,
        help="Тишина между чанками (ffmpeg), мс. 0 = склейка встык",
    )
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.exists():
        sys.exit(f"Нет файла: {inp}")
    text = inp.read_text(encoding="utf-8").strip()
    if not text:
        sys.exit("Пустой файл")

    text = text.replace("—", "-")
    if not args.plain:
        paras = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
        text = "\n\n".join(
            wrap_each_word_before_question(p) if "*" not in p else p for p in paras
        )
    chunks = split_text_to_chunks(text, args.max_chunk)
    out = Path(args.output) if args.output else inp.with_suffix(".wav")

    print(f"Чанков: {len(chunks)}, пауза между чанками: {args.pause_ms} мс")
    model, _ = torch.hub.load(
        repo_or_dir="snakers4/silero-models",
        model="silero_tts",
        language="ru",
        speaker=args.model,
        trust_repo=True,
    )

    tmp = inp.parent / ".silero_tmp"
    subprocess.run(["rm", "-rf", str(tmp)], check=False)
    tmp.mkdir(exist_ok=True)

    parts: list[Path] = []
    for i, ch in enumerate(chunks, 1):
        p = tmp / f"part_{i:04d}.wav"
        model.save_wav(text=ch, speaker=args.speaker, sample_rate=args.sample_rate, audio_path=str(p))
        parts.append(p)

    concat_wavs(parts, out, tmp, args.pause_ms, args.sample_rate)
    subprocess.run(["rm", "-rf", str(tmp)], check=False)
    print(f"Готово: {out}")


if __name__ == "__main__":
    main()
