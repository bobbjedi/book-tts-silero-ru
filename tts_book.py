#!/usr/bin/env python3
"""Конвертация TXT в аудиокнигу через edge-tts с интонацией."""

import argparse
import asyncio
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import edge_tts

DEFAULT_VOICE = "ru-RU-DmitryNeural"
MAX_CHUNK = 3000

PROSODY = {
    "narration":    {"pitch": "-2Hz",  "rate_offset": -15},
    "author":       {"pitch": "-2Hz",  "rate_offset": -15},
    "dialogue":     {"pitch": "+5Hz",  "rate_offset": +5},
    "question":     {"pitch": "+7Hz",  "rate_offset": +7},
    "exclamation":  {"pitch": "+12Hz", "rate_offset": +40},
    "qexclaim":     {"pitch": "+25Hz", "rate_offset": +35},
    "pause":        {"pitch": "+0Hz",  "rate_offset": 0},
}

LABELS = {
    "narration": "описание",
    "author": "автор",
    "dialogue": "реплика",
    "question": "вопрос",
    "exclamation": "восклицание",
    "qexclaim": "вопрос-восклицание",
}


@dataclass
class Segment:
    text: str
    seg_type: str


@dataclass
class Paragraph:
    raw_text: str
    segments: list[Segment]


def _classify_text(text: str, is_speech: bool = False) -> str:
    """Определяет тип по завершающей пунктуации."""
    stripped = text.strip()
    if re.search(r'[?!]{2,}', stripped) or "?!" in stripped or "!?" in stripped:
        return "qexclaim"
    if stripped.endswith("?"):
        return "question"
    if stripped.endswith("!"):
        return "exclamation"
    return "dialogue" if is_speech else "narration"


def _split_dialogue_line(line: str) -> list[Segment]:
    """Разбивает '— реплика — ремарка — реплика' на сегменты."""
    stripped = re.sub(r'^[—–\-]\s*', '', line.strip())
    # Диалоговые разделители бывают '—' или '-' (как в некоторых книгах/переводах).
    # Для '-' режем только если он окружён пробелами, чтобы не ломать дефисы в словах.
    parts = [p for p in re.split(r'\s*[—–]\s*|\s+-\s+', stripped) if p is not None]

    if len(parts) == 1:
        return [Segment(parts[0], _classify_text(parts[0], is_speech=True))]

    merged: list[tuple[str, bool]] = []  # (text, is_speech)
    for i, part in enumerate(parts):
        part = part.strip()
        if not part:
            continue
        if i == 0:
            merged.append((part, True))
        elif merged and merged[-1][1]:
            prev_text = merged[-1][0]
            prev_ends = bool(re.search(r'[.!?…]$', prev_text))
            if prev_ends and part[0].islower():
                merged.append((part, False))
            elif not prev_ends:
                merged[-1] = (f"{prev_text} — {part}", True)
            else:
                merged.append((part, True))
        else:
            merged.append((part, True))

    segments: list[Segment] = []
    for text, is_speech in merged:
        if is_speech:
            segments.append(Segment(text, _classify_text(text, is_speech=True)))
        else:
            segments.append(Segment(text, "author"))
    return segments


def parse_segments(text: str) -> list[Segment]:
    """Разбивает текст на типизированные сегменты с паузами между абзацами."""
    paragraphs = re.split(r'\n\s*\n', text.strip())
    segments: list[Segment] = []

    for pidx, para in enumerate(paragraphs):
        para = para.strip()
        if not para:
            continue
        if pidx > 0:
            segments.append(Segment("", "pause"))

        lines = [ln.strip() for ln in para.split('\n') if ln.strip()]
        for line in lines:
            if re.match(r'^[—–\-]', line):
                segments.extend(_split_dialogue_line(line))
            else:
                segments.append(Segment(line, _classify_text(line)))

    return segments


def parse_paragraphs(text: str) -> list[Paragraph]:
    """Парсит абзацы, сохраняя и raw-текст, и сегменты для логов."""
    paragraphs = re.split(r'\n\s*\n', text.strip())
    out: list[Paragraph] = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        segs: list[Segment] = []
        lines = [ln.strip() for ln in para.split('\n') if ln.strip()]
        for line in lines:
            if re.match(r'^[—–\-]', line):
                segs.extend(_split_dialogue_line(line))
            else:
                segs.append(Segment(line, _classify_text(line)))
        raw = "\n".join(lines)
        out.append(Paragraph(raw_text=raw, segments=segs))
    return out


def split_long_segment(seg: Segment, max_len: int = MAX_CHUNK) -> list[Segment]:
    if len(seg.text) <= max_len:
        return [seg]
    sentences = re.split(r'(?<=[.!?…»])\s+', seg.text)
    parts: list[Segment] = []
    buf = ""
    for s in sentences:
        if len(buf) + len(s) + 1 > max_len:
            if buf:
                parts.append(Segment(buf.strip(), seg.seg_type))
            buf = s
        else:
            buf = f"{buf} {s}" if buf else s
    if buf.strip():
        parts.append(Segment(buf.strip(), seg.seg_type))
    return parts


def _strip_stars(s: str) -> str:
    """Edge не озвучивает * адекватно — убираем из текста."""
    return s.replace("*", "")


async def synthesize_segment(seg: Segment, voice: str, output: str,
                              base_rate: int, volume: str, retries: int = 3):
    p = PROSODY.get(seg.seg_type, PROSODY["narration"])
    rate = f"{base_rate + p['rate_offset']:+d}%"
    pitch = p["pitch"]
    raw = _strip_stars(seg.text)
    text = re.sub(r'[?!]{2,}', '!', raw) if seg.seg_type == "qexclaim" else raw
    for attempt in range(retries):
        try:
            comm = edge_tts.Communicate(text, voice, rate=rate, volume=volume, pitch=pitch)
            await comm.save(output)
            return
        except Exception:
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)
            else:
                raise


def generate_silence_mp3(output: str, duration_ms: int = 600):
    """Генерирует тишину через ffmpeg."""
    import subprocess
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", "anullsrc=r=24000:cl=mono",
        "-t", str(duration_ms / 1000),
        "-c:a", "libmp3lame", "-b:a", "48k",
        output
    ], capture_output=True, check=True)


async def main():
    parser = argparse.ArgumentParser(description="TXT → аудиокнига (edge-tts)")
    parser.add_argument("input", help="Путь к .txt файлу")
    parser.add_argument("-o", "--output", help="Выходной .mp3")
    parser.add_argument("-v", "--voice", default=DEFAULT_VOICE,
                        help=f"Голос (по умолчанию {DEFAULT_VOICE})")
    parser.add_argument("-r", "--rate", default="+0%",
                        help="Базовая скорость, напр. +30%% = 1.3x (по умолчанию +0%%)")
    parser.add_argument("--volume", default="+0%", help="Громкость, напр. +50%%")
    parser.add_argument(
        "--batch",
        choices=["segments", "paragraphs"],
        default="paragraphs",
        help="Как отправлять в TTS: segments (много запросов) или paragraphs (1 запрос на абзац)",
    )
    parser.add_argument(
        "--log-full",
        action="store_true",
        help="Печатать полный текст каждого отправленного куска (иначе только превью)",
    )
    parser.add_argument("--list-voices", action="store_true",
                        help="Показать доступные голоса")
    args = parser.parse_args()

    if args.list_voices:
        voices = await edge_tts.list_voices()
        for v in voices:
            print(f"{v['ShortName']:40s} {v['Locale']:10s} {v['Gender']}")
        return

    input_path = Path(args.input)
    if not input_path.exists():
        sys.exit(f"Файл не найден: {input_path}")

    text = input_path.read_text(encoding="utf-8").strip()
    if not text:
        sys.exit("Файл пуст")

    output_path = Path(args.output) if args.output else input_path.with_suffix(".mp3")
    base_rate = int(re.match(r'([+-]?\d+)', args.rate).group(1))

    paragraphs = parse_paragraphs(text)
    log_segments: list[Segment] = []
    for i, p in enumerate(paragraphs):
        if i > 0:
            log_segments.append(Segment("", "pause"))
        log_segments.extend(p.segments)

    if args.batch == "segments":
        flat: list[Segment] = []
        for seg in log_segments:
            if seg.seg_type == "pause":
                flat.append(seg)
            else:
                flat.extend(split_long_segment(seg))
    else:
        # Синтезируем 1 запрос на абзац (разметка — только в логах).
        flat = []
        for i, p in enumerate(paragraphs):
            if i > 0:
                flat.append(Segment("", "pause"))
            # Важно: оставляем переносы строк — они помогают паузам внутри абзаца.
            flat.append(Segment(p.raw_text, "narration"))

    temp_dir = input_path.parent / ".tts_tmp"
    temp_dir.mkdir(exist_ok=True)

    pause_path = temp_dir / "pause.mp3"
    speed_multiplier = 1 + base_rate / 100
    pause_ms = max(100, int(300 / speed_multiplier))
    generate_silence_mp3(str(pause_path), pause_ms)
    print(f"Скорость {speed_multiplier:.1f}x, пауза {pause_ms}мс\n")
    pause_bytes = pause_path.read_bytes()

    total_audio = len([s for s in flat if s.seg_type != "pause"])
    audio_idx = 0
    part_files: list[Path] = []

    for i, seg in enumerate(flat):
        if seg.seg_type == "pause":
            p = temp_dir / f"pause_{i:04d}.mp3"
            p.write_bytes(pause_bytes)
            part_files.append(p)
        else:
            audio_idx += 1
            p = temp_dir / f"part_{i:04d}.mp3"
            label = LABELS.get(seg.seg_type, seg.seg_type)
            to_print = seg.text if args.log_full else (seg.text[:80] + ("..." if len(seg.text) > 80 else ""))
            if args.batch == "paragraphs":
                # Печатаем подробную разметку для проверки
                print(f"  [{audio_idx}/{total_audio}] [абзац]: {to_print}")
                # Покажем сегменты этого абзаца (валидируем разметку глазами)
                # (по индексу аудио мы не всегда можем восстановить абзац, поэтому печатаем всё заранее ниже)
            else:
                print(f"  [{audio_idx}/{total_audio}] [{label}]: {to_print}")
            await synthesize_segment(seg, args.voice, str(p), base_rate, args.volume)
            part_files.append(p)

    print("\nСклеиваю...")
    with open(output_path, "wb") as out:
        for pf in part_files:
            out.write(pf.read_bytes())

    shutil.rmtree(temp_dir)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"Готово: {output_path} ({size_mb:.1f} МБ)")

    # Вывод разметки после синтеза (чтобы не мешать прогрессу)
    if args.batch == "paragraphs":
        print("\nРазметка (для проверки):")
        total = len([s for s in log_segments if s.seg_type != "pause"])
        idx = 0
        for seg in log_segments:
            if seg.seg_type == "pause":
                continue
            idx += 1
            label = LABELS.get(seg.seg_type, seg.seg_type)
            print(f"[{label}]: {seg.text}")


if __name__ == "__main__":
    asyncio.run(main())
