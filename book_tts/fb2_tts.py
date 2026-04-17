"""Озвучка FB2 по главам в MP3."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import torch

from .audio_pitch import apply_post_tone_wav
from .parser import Chunk, parse_book_text

FB2_NS = {"fb": "http://www.gribuser.ru/xml/fictionbook/2.0"}


def _log(message: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print("[{0}] {1}".format(ts, message), flush=True)


def _safe_name(name: str, max_len: int = 120) -> str:
    s = re.sub(r"\s+", " ", name).strip()
    s = re.sub(r"[\\/:*?\"<>|]", "_", s)
    s = s.strip(". ")
    if not s:
        s = "chapter"
    return s[:max_len].rstrip(". ")


def _short_chapter_title(title: str) -> str:
    t = re.sub(r"\s+", " ", title).strip()
    # Приводим к формату, начинающемуся с "Глава XXX ...".
    m = re.search(r"(глава\s+\d+)\s*[:.]?\s*(.*)$", t, flags=re.IGNORECASE)
    if not m:
        return t
    head = m.group(1).title()
    tail = m.group(2).strip()
    return "{0} {1}".format(head, tail).strip()


def _extract_title(section: ET.Element) -> str:
    title_node = section.find("fb:title", FB2_NS)
    if title_node is None:
        return ""
    parts: List[str] = []
    for p in title_node.findall("fb:p", FB2_NS):
        txt = "".join(p.itertext()).strip()
        if txt:
            parts.append(txt)
    return " ".join(parts).strip()


def _extract_section_text(section: ET.Element) -> str:
    paragraphs: List[str] = []
    for p in section.findall(".//fb:p", FB2_NS):
        txt = "".join(p.itertext()).strip()
        if txt:
            paragraphs.append(txt)
    return "\n\n".join(paragraphs)


def extract_fb2_chapters(fb2_path: Path) -> List[Tuple[str, str]]:
    tree = ET.parse(str(fb2_path))
    root = tree.getroot()

    bodies = root.findall("fb:body", FB2_NS)
    if not bodies:
        return []

    main_body = bodies[0]
    top_sections = main_body.findall("fb:section", FB2_NS)

    chapters: List[Tuple[str, str]] = []
    for idx, section in enumerate(top_sections, 1):
        title = _extract_title(section) or "Глава {0}".format(idx)
        text = _extract_section_text(section)
        if text.strip():
            chapters.append((title, text))

    if chapters:
        return chapters

    # fallback: нет top-level section, читаем весь body как одну главу
    body_paras: List[str] = []
    for p in main_body.findall(".//fb:p", FB2_NS):
        txt = "".join(p.itertext()).strip()
        if txt:
            body_paras.append(txt)
    full_text = "\n\n".join(body_paras).strip()
    return [("Книга", full_text)] if full_text else []


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


def _wav_to_mp3(wav_path: Path, mp3_path: Path, bitrate: str = "192k") -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav_path), "-codec:a", "libmp3lame", "-b:a", bitrate, str(mp3_path)],
        check=True,
        capture_output=True,
    )


def _synthesize_chapter_wav(
    model,
    chapter_name: str,
    chapter_text: str,
    wav_out: Path,
    work_root: Path,
    speaker: str,
    sample_rate: int,
    pause_sec: float,
    max_chars: int,
) -> None:
    chunks = parse_book_text(chapter_text, max_chars=max_chars)
    if not chunks:
        raise ValueError("Пустой набор чанков")
    _log("Глава '{0}': чанков после парсера = {1}".format(chapter_name, len(chunks)))

    chapter_key = hashlib.sha1((chapter_name + "\n" + chapter_text).encode("utf-8")).hexdigest()[:16]
    chapter_work_dir = work_root / chapter_key
    parts_dir = chapter_work_dir / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)

    state_path = chapter_work_dir / "state.json"
    state = {
        "chapter_name": chapter_name,
        "chunk_count": len(chunks),
        "speaker": speaker,
        "sample_rate": sample_rate,
        "pause_sec": pause_sec,
        "max_chars": max_chars,
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    parts: List[Path] = []
    for idx, chunk in enumerate(chunks, 1):
        part_path = parts_dir / "part_{0:05d}.wav".format(idx)
        skip_marker = parts_dir / "part_{0:05d}.skip".format(idx)

        if skip_marker.exists():
            _log("  ch#{0}: skip marker".format(idx))
            continue
        if part_path.exists() and part_path.stat().st_size > 0:
            _log("  ch#{0}: reuse wav".format(idx))
            parts.append(part_path)
            continue

        if _save_chunk_wav(model, chunk, part_path, speaker, sample_rate):
            skip_marker.unlink(missing_ok=True)
            _log("  ch#{0}: generated wav".format(idx))
            parts.append(part_path)
        else:
            skip_marker.write_text("skip", encoding="utf-8")
            _log("  ch#{0}: marked as skip".format(idx))

    if not parts:
        raise ValueError("В главе нет озвучиваемого текста")
    _log("Глава '{0}': concat {1} частей".format(chapter_name, len(parts)))
    _concat_wavs(parts, wav_out, chapter_work_dir, pause_sec, sample_rate)


def _normalize_chunk_text_for_tts(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    # Декоративные разделители вида "***" не озвучиваем.
    cleaned = re.sub(r"^\*+(?:\s+\*+)*[.!?…]*$", "", cleaned).strip()
    # Проблемные кавычки для некоторых версий tokenizer Silero.
    cleaned = cleaned.replace('"', "")
    return cleaned


def _apply_chunk_post_tone(part_path: Path, chunk: Chunk, sample_rate: int) -> None:
    pt = (chunk.post_tone or "").strip()
    if not pt:
        return
    tmp = part_path.with_name(part_path.stem + "_pt.wav")
    apply_post_tone_wav(part_path, tmp, pt, sample_rate)
    shutil.move(str(tmp), str(part_path))


def _strip_unsupported_chars_by_model(text: str, model) -> str:
    symbols = getattr(model, "symbols", None)
    if not isinstance(symbols, str) or not symbols:
        return text
    allowed = set(symbols)
    cleaned = "".join(ch for ch in text.lower() if ch in allowed)
    return re.sub(r"\s+", " ", cleaned).strip()


def _save_chunk_wav(model, chunk: Chunk, part_path: Path, speaker: str, sample_rate: int) -> bool:
    original_text = chunk.text.strip()
    text = _normalize_chunk_text_for_tts(original_text)
    if not text:
        return False

    if chunk.ssml and text == original_text:
        ssml_text = chunk.ssml
        try:
            model.save_wav(
                ssml_text=ssml_text,
                speaker=speaker,
                sample_rate=sample_rate,
                audio_path=str(part_path),
                put_yo=True,
            )
            _apply_chunk_post_tone(part_path, chunk, sample_rate)
            return True
        except Exception:
            # Для нестабильных SSML-кейсов fallback в plain text.
            _log("  fallback to plain text (SSML parse error)")
            pass

    try:
        model.save_wav(
            text=text,
            speaker=speaker,
            sample_rate=sample_rate,
            audio_path=str(part_path),
            put_yo=True,
        )
        _apply_chunk_post_tone(part_path, chunk, sample_rate)
        return True
    except Exception as exc:
        safe_text = _strip_unsupported_chars_by_model(text, model)
        if safe_text and safe_text != text:
            _log("  sanitize unsupported chars and retry")
            try:
                model.save_wav(
                    text=safe_text,
                    speaker=speaker,
                    sample_rate=sample_rate,
                    audio_path=str(part_path),
                    put_yo=True,
                )
                _apply_chunk_post_tone(part_path, chunk, sample_rate)
                return True
            except Exception as exc2:
                _log("  sanitized retry failed: {0}".format(exc2))
        _log("  chunk failed permanently: {0}".format(exc))
        return False


def synthesize_fb2_to_mp3_chapters(
    fb2_path: Path,
    output_dir: Path,
    model_name: str = "v5_4_ru",
    speaker: str = "xenia",
    sample_rate: int = 48000,
    pause_sec: float = 0.022,
    max_chars: int = 850,
) -> Path:
    chapters = extract_fb2_chapters(fb2_path)
    if not chapters:
        raise ValueError("Не удалось извлечь главы из FB2")
    _log("Извлечено глав: {0}".format(len(chapters)))

    output_dir.mkdir(parents=True, exist_ok=True)
    work_root = output_dir / ".fb2_tts_work"
    work_root.mkdir(parents=True, exist_ok=True)

    model, _ = torch.hub.load(
        repo_or_dir="snakers4/silero-models",
        model="silero_tts",
        language="ru",
        speaker=model_name,
        trust_repo=True,
    )
    _log("Модель загружена: {0}, голос: {1}, pause_sec={2}".format(model_name, speaker, pause_sec))

    for idx, (title, text) in enumerate(chapters, 1):
        short_title = _short_chapter_title(title)
        safe_title = _safe_name(short_title)
        base_name = safe_title
        wav_path = output_dir / "{0}.wav".format(base_name)
        mp3_path = output_dir / "{0}.mp3".format(base_name)
        chapter_key = hashlib.sha1((title + "\n" + text).encode("utf-8")).hexdigest()[:16]
        chapter_work_dir = work_root / chapter_key

        if mp3_path.exists() and mp3_path.stat().st_size > 0:
            _log("[{0}/{1}] skip existing {2}".format(idx, len(chapters), mp3_path.name))
            continue

        _log("[{0}/{1}] start {2}".format(idx, len(chapters), base_name))
        _synthesize_chapter_wav(
            model=model,
            chapter_name=title,
            chapter_text=text,
            wav_out=wav_path,
            work_root=work_root,
            speaker=speaker,
            sample_rate=sample_rate,
            pause_sec=pause_sec,
            max_chars=max_chars,
        )
        _log("[{0}/{1}] convert wav->mp3".format(idx, len(chapters)))
        _wav_to_mp3(wav_path, mp3_path)
        wav_path.unlink(missing_ok=True)
        if chapter_work_dir.exists():
            shutil.rmtree(chapter_work_dir)
        _log("[{0}/{1}] done {2}".format(idx, len(chapters), mp3_path.name))

    return output_dir


def main() -> None:
    ap = argparse.ArgumentParser(description="Озвучка FB2 по главам в MP3")
    ap.add_argument("input", help="Входной .fb2 файл")
    ap.add_argument("-o", "--output-dir", help="Директория для chapter mp3")
    ap.add_argument("--model", default="v5_5_ru", help="Модель Silero")
    ap.add_argument("--speaker", default="eugene", help="Голос")
    ap.add_argument("--sample-rate", type=int, default=48000, help="Частота дискретизации")
    ap.add_argument("--pause-sec", type=float, default=0.022, help="Пауза между чанками в секундах")
    ap.add_argument("--max-chars", type=int, default=850, help="Макс. длина чанка для парсера")
    args = ap.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        sys.exit("Нет файла: {0}".format(input_path))
    if input_path.suffix.lower() != ".fb2":
        sys.exit("Ожидается .fb2 файл")

    out_dir = Path(args.output_dir) if args.output_dir else input_path.with_name("{0}_mp3".format(input_path.stem))

    try:
        result_dir = synthesize_fb2_to_mp3_chapters(
            fb2_path=input_path,
            output_dir=out_dir,
            model_name=args.model,
            speaker=args.speaker,
            sample_rate=args.sample_rate,
            pause_sec=args.pause_sec,
            max_chars=args.max_chars,
        )
    except ET.ParseError as exc:
        sys.exit("Некорректный FB2/XML: {0}".format(exc))
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError) as exc:
        sys.exit("Ошибка: {0}".format(exc))

    print("Готово: {0}".format(result_dir))


if __name__ == "__main__":
    main()
