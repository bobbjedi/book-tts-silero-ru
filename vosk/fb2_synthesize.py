"""Озвучка FB2 по главам через vosk-tts."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from book_tts.num_utils import normalize_yo_to_e

from .chunker import chunk_text_for_vosk
from .text_utils import replace_numbers_ru

FB2_NS = {"fb": "http://www.gribuser.ru/xml/fictionbook/2.0"}
_WORKER_MODEL = None
_WORKER_SYNTH = None
_WORKER_ACCENT = None


def _log(message: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print("[{0}] {1}".format(ts, message), flush=True)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_dotenv_simple(path: Path) -> None:
    """Минимальный .env: KEY=VALUE, без экспорта shell-специфики."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if not key:
            continue
        if key not in os.environ:
            os.environ[key] = val


def _default_ruaccent_models_dir() -> Path:
    """Общий каталог моделей ruaccent (вне output/work)."""
    env_dir = os.environ.get("RUACCENT_MODELS_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser()
    xdg_cache_home = os.environ.get("XDG_CACHE_HOME", "").strip()
    cache_root = Path(xdg_cache_home).expanduser() if xdg_cache_home else (Path.home() / ".cache")
    return cache_root / "book-tts-silero-ru" / "ruaccent_models"


def _safe_name(name: str, max_len: int = 120) -> str:
    s = re.sub(r"\s+", " ", name).strip()
    s = re.sub(r"[\\/:*?\"<>|]", "_", s)
    s = s.strip(". ")
    if not s:
        s = "chapter"
    return s[:max_len].rstrip(". ")


def _short_chapter_title(title: str) -> str:
    t = re.sub(r"\s+", " ", title).strip()
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


def _extract_section_paragraphs(section: ET.Element) -> List[str]:
    paragraphs: List[str] = []
    for p in section.findall(".//fb:p", FB2_NS):
        txt = "".join(p.itertext()).strip()
        if txt:
            paragraphs.append(txt)
    return paragraphs


def extract_fb2_chapters(fb2_path: Path) -> List[Tuple[str, List[str]]]:
    tree = ET.parse(str(fb2_path))
    root = tree.getroot()

    bodies = root.findall("fb:body", FB2_NS)
    if not bodies:
        return []

    main_body = bodies[0]
    top_sections = main_body.findall("fb:section", FB2_NS)

    chapters: List[Tuple[str, List[str]]] = []
    for idx, section in enumerate(top_sections, 1):
        title = _extract_title(section) or "Глава {0}".format(idx)
        paragraphs = _extract_section_paragraphs(section)
        if paragraphs:
            chapters.append((title, paragraphs))

    if chapters:
        return chapters

    body_paras: List[str] = []
    for p in main_body.findall(".//fb:p", FB2_NS):
        txt = "".join(p.itertext()).strip()
        if txt:
            body_paras.append(txt)
    return [("Книга", body_paras)] if body_paras else []


def _normalize_for_vosk(text: str) -> str:
    t = text.strip().replace("*", "")
    # ё оставляем — без неё сеть читает «всЕ» вместо «всЁ»
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
        .replace("…", ".")
        .replace("...", ".")
        .replace(". . .", ".")
    )
    t = re.sub(r"\.{3,}", ".", t)
    return re.sub(r"\s+", " ", t).strip()


def _normalize_for_ruaccent(text: str) -> str:
    # Минимальная нормализация, чтобы не ломать токенизацию ruaccent.
    t = text.strip()
    t = (
        t.replace("„", '"')
        .replace("“", '"')
        .replace("”", '"')
        .replace("«", '"')
        .replace("»", '"')
        .replace("‘", "'")
        .replace("’", "'")
    )
    return re.sub(r"\s+", " ", t).strip()


def _maybe_accent_paragraphs(paragraphs: List[str], use_accent: bool) -> List[str]:
    global _WORKER_ACCENT
    if not use_accent:
        return paragraphs
    if _WORKER_ACCENT is None:
        raise RuntimeError("accentizer is not initialized")
    out: List[str] = []
    for p in paragraphs:
        prepared = _normalize_for_ruaccent(p)
        if not prepared:
            continue
        out.append(_WORKER_ACCENT.process_all(prepared))
    return out


def _strip_unsupported_chars_by_model(text: str, model) -> str:
    symbols = model.config.get("phoneme_id_map", {})
    allowed = set(symbols.keys())
    if not allowed:
        return text
    cleaned = "".join(ch for ch in text.lower() if ch in allowed or ch.isspace())
    return re.sub(r"\s+", " ", cleaned).strip()


def _build_vosk_chunks_from_paragraphs(paragraphs: List[str], max_chars: int) -> List[str]:
    prepared: List[str] = []
    for para in paragraphs:
        p = replace_numbers_ru(para.strip())
        if p:
            prepared.append(p)
    if not prepared:
        return []
    # Прогоняем весь поток сразу, чтобы доклейка работала и между абзацами.
    return chunk_text_for_vosk("\n\n".join(prepared), max_chars=max_chars)


def _load_cached_chunks(chunks_json_path: Path, max_chars: int) -> List[str]:
    if not chunks_json_path.exists():
        return []
    try:
        payload = json.loads(chunks_json_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    if not isinstance(payload, dict):
        return []
    if int(payload.get("max_chars", -1)) != max_chars:
        return []
    chunks = payload.get("chunks")
    if not isinstance(chunks, list):
        return []
    clean = [str(x).strip() for x in chunks if str(x).strip()]
    return clean


def _save_chunks_cache(chapter_work_dir: Path, chunks: List[str], max_chars: int) -> None:
    chunks_json_path = chapter_work_dir / "chunks.json"
    chunks_txt_path = chapter_work_dir / "chunks.txt"
    chunks_json_path.write_text(
        json.dumps(
            {
                "max_chars": max_chars,
                "chunk_count": len(chunks),
                "chunks": chunks,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    chunks_txt_path.write_text("\n\n".join(chunks), encoding="utf-8")


def _load_json_dict(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _chunks_hash(chunks: List[str]) -> str:
    return hashlib.sha1("\n<chunk>\n".join(chunks).encode("utf-8")).hexdigest()


def _reset_parts_dir(parts_dir: Path, reason: str) -> None:
    if parts_dir.exists():
        shutil.rmtree(parts_dir)
    parts_dir.mkdir(parents=True, exist_ok=True)
    _log("reset parts dir: {0}".format(reason))


def _write_skip_marker(skip_marker: Path, reason: str, chunk_text: str) -> None:
    payload = {
        "reason": reason,
        "chunk_text": chunk_text,
    }
    skip_marker.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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


def _wav_to_mp3(wav_path: Path, mp3_path: Path, bitrate: str = "192k") -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav_path), "-codec:a", "libmp3lame", "-b:a", bitrate, str(mp3_path)],
        check=True,
        capture_output=True,
    )


def _save_chunk_wav(synth, model, chunk_text: str, part_path: Path, speaker_id: int) -> bool:
    prepared = _normalize_for_vosk(chunk_text)
    if not prepared:
        return False
    try:
        synth.synth(prepared, str(part_path), speaker_id=speaker_id)
        return True
    except Exception as exc:
        safe = _strip_unsupported_chars_by_model(prepared, model)
        if safe and safe != prepared:
            try:
                synth.synth(safe, str(part_path), speaker_id=speaker_id)
                return True
            except Exception as exc2:
                _log("  sanitized retry failed: {0}".format(exc2))
        _log("  chunk failed permanently: {0}".format(exc))
        return False


def _synthesize_chapter(
    model,
    synth,
    chapter_title: str,
    paragraphs: List[str],
    output_dir: Path,
    work_root: Path,
    speaker_id: int,
    max_chars: int,
    pause_sec: float,
    use_accent: bool,
) -> Path:
    chapter_started = time.monotonic()
    short_title = _short_chapter_title(chapter_title)
    safe_title = _safe_name(short_title)
    base_name = safe_title
    wav_path = output_dir / "{0}.wav".format(base_name)
    mp3_path = output_dir / "{0}.mp3".format(base_name)

    if mp3_path.exists() and mp3_path.stat().st_size > 0:
        _log("skip existing {0}".format(mp3_path.name))
        return mp3_path

    if use_accent:
        paragraphs = _maybe_accent_paragraphs(paragraphs, use_accent=True)
    chapter_text = "\n\n".join(paragraphs)
    chapter_key = hashlib.sha1((chapter_title + "\n" + chapter_text).encode("utf-8")).hexdigest()[:16]
    chapter_work_dir = work_root / chapter_key
    parts_dir = chapter_work_dir / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)

    chunks_json_path = chapter_work_dir / "chunks.json"
    chunks = _load_cached_chunks(chunks_json_path, max_chars=max_chars)
    chunks_from_cache = bool(chunks)
    if chunks:
        _log("reuse chunks cache: {0}".format(chunks_json_path.name))
    else:
        chunks = _build_vosk_chunks_from_paragraphs(paragraphs, max_chars=max_chars)
        if chunks:
            _save_chunks_cache(chapter_work_dir, chunks, max_chars=max_chars)

    if not chunks:
        raise ValueError("Пустой набор чанков в главе")
    _log("start {0}: chunks={1}, accent={2}".format(base_name, len(chunks), "yes" if use_accent else "no"))

    state_path = chapter_work_dir / "state.json"
    chunks_hash = _chunks_hash(chunks)
    old_state = _load_json_dict(state_path)
    if not chunks_from_cache:
        _reset_parts_dir(parts_dir, "rebuild chunks cache")
    elif old_state is not None:
        old_sig = (
            old_state.get("speaker_id"),
            old_state.get("max_chars"),
            old_state.get("pause_sec"),
            old_state.get("use_accent"),
            old_state.get("chunk_count"),
            old_state.get("chunks_hash"),
        )
        new_sig = (speaker_id, max_chars, pause_sec, bool(use_accent), len(chunks), chunks_hash)
        if old_sig != new_sig:
            _reset_parts_dir(parts_dir, "config or chunk signature changed")

    state_path.write_text(
        json.dumps(
            {
                "chapter_title": chapter_title,
                "chunk_count": len(chunks),
                "speaker_id": speaker_id,
                "max_chars": max_chars,
                "pause_sec": pause_sec,
                "use_accent": bool(use_accent),
                "chunks_hash": chunks_hash,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    parts: List[Path] = []
    for idx, chunk in enumerate(chunks, 1):
        chunk_started = time.monotonic()
        part_path = parts_dir / "part_{0:05d}.wav".format(idx)
        skip_marker = parts_dir / "part_{0:05d}.skip".format(idx)
        chunk_label = "chunk {0:05d}/{1:05d}".format(idx, len(chunks))

        if skip_marker.exists():
            elapsed = time.monotonic() - chunk_started
            _log("  {0} skip-marker ({1:.2f}s)".format(chunk_label, elapsed))
            continue
        if part_path.exists() and part_path.stat().st_size > 0:
            parts.append(part_path)
            elapsed = time.monotonic() - chunk_started
            _log("  {0} reuse ({1:.2f}s)".format(chunk_label, elapsed))
            continue

        if _save_chunk_wav(synth, model, chunk, part_path, speaker_id):
            parts.append(part_path)
            elapsed = time.monotonic() - chunk_started
            _log("  {0} ok ({1:.2f}s)".format(chunk_label, elapsed))
        else:
            _write_skip_marker(skip_marker, "synthesis_failed", chunk)
            elapsed = time.monotonic() - chunk_started
            _log("  {0} failed -> .skip ({1:.2f}s)".format(chunk_label, elapsed))

    if not parts:
        raise ValueError("Нет озвучиваемых чанков в главе")

    sample_rate = _probe_sample_rate(parts[0])
    _concat_wavs(parts, wav_path, chapter_work_dir, pause_sec, sample_rate)
    _wav_to_mp3(wav_path, mp3_path)
    wav_path.unlink(missing_ok=True)
    if chapter_work_dir.exists():
        shutil.rmtree(chapter_work_dir)
    chapter_elapsed = time.monotonic() - chapter_started
    _log("done {0}; chapter_runtime={1:.2f}s".format(mp3_path.name, chapter_elapsed))
    return mp3_path


def _worker_init(
    model_name: str,
    use_accent: bool,
    accent_model_size: str,
    accent_use_dictionary: bool,
    accent_models_dir: str,
) -> None:
    global _WORKER_MODEL, _WORKER_SYNTH, _WORKER_ACCENT
    from vosk_tts import Model, Synth

    _WORKER_MODEL = Model(model_name=model_name)
    _WORKER_SYNTH = Synth(_WORKER_MODEL)
    _WORKER_ACCENT = None
    if use_accent:
        from ruaccent import RUAccent

        acc = RUAccent()
        acc.load(
            omograph_model_size=accent_model_size,
            use_dictionary=accent_use_dictionary,
            workdir=accent_models_dir or None,
            repo="ruaccent/accentuator",
        )
        _WORKER_ACCENT = acc


def _process_chapter_task(
    chapter_idx: int,
    chapters_total: int,
    chapter_title: str,
    paragraphs: List[str],
    output_dir_str: str,
    work_root_str: str,
    speaker_id: int,
    max_chars: int,
    pause_sec: float,
    use_accent: bool,
) -> Tuple[int, str, float]:
    global _WORKER_MODEL, _WORKER_SYNTH
    if _WORKER_MODEL is None or _WORKER_SYNTH is None:
        raise RuntimeError("worker model is not initialized")

    started = time.monotonic()
    _log("[{0}/{1}] {2}".format(chapter_idx, chapters_total, chapter_title))
    result_path = _synthesize_chapter(
        model=_WORKER_MODEL,
        synth=_WORKER_SYNTH,
        chapter_title=chapter_title,
        paragraphs=paragraphs,
        output_dir=Path(output_dir_str),
        work_root=Path(work_root_str),
        speaker_id=speaker_id,
        max_chars=max_chars,
        pause_sec=pause_sec,
        use_accent=use_accent,
    )
    elapsed = time.monotonic() - started
    return chapter_idx, str(result_path), elapsed


def synthesize_fb2_to_mp3_chapters(
    fb2_path: Path,
    output_dir: Path,
    model_name: str = "vosk-model-tts-ru-0.9-multi",
    speaker_id: int = 3,
    max_chars: int = 250,
    pause_sec: float = 0.03,
    workers: int = 1,
    accent: bool = False,
    accent_model_size: str = "turbo3.1",
    accent_use_dictionary: bool = True,
    accent_models_dir: Optional[Path] = None,
) -> Path:
    total_started = time.monotonic()
    chapters = extract_fb2_chapters(fb2_path)
    if not chapters:
        raise ValueError("Не удалось извлечь главы из FB2")
    if workers < 1:
        raise ValueError("workers должен быть >= 1")

    output_dir.mkdir(parents=True, exist_ok=True)
    work_root = output_dir / ".vosk_fb2_work"
    work_root.mkdir(parents=True, exist_ok=True)

    if accent:
        # Держим ruaccent-модели в одном общем каталоге, кэш/докачку делает сама библиотека.
        models_dir = accent_models_dir.expanduser() if accent_models_dir else _default_ruaccent_models_dir()
        models_dir.mkdir(parents=True, exist_ok=True)
        _log("ruaccent models dir: {0}".format(models_dir))
    else:
        models_dir = None

    _log(
        "chapters={0}, speaker_id={1}, max_chars={2}, pause={3}, workers={4}, accent={5}".format(
            len(chapters), speaker_id, max_chars, pause_sec, workers, bool(accent)
        )
    )
    if workers == 1:
        from vosk_tts import Model, Synth

        model = Model(model_name=model_name)
        synth = Synth(model)
        global _WORKER_ACCENT
        _WORKER_ACCENT = None
        if accent:
            from ruaccent import RUAccent

            acc = RUAccent()
            acc.load(
                omograph_model_size=accent_model_size,
                use_dictionary=accent_use_dictionary,
                workdir=str(models_dir) if models_dir is not None else None,
                repo="ruaccent/accentuator",
            )
            _WORKER_ACCENT = acc
        for i, (title, paragraphs) in enumerate(chapters, 1):
            chapter_started = time.monotonic()
            _log("[{0}/{1}] {2}".format(i, len(chapters), title))
            _synthesize_chapter(
                model=model,
                synth=synth,
                chapter_title=title,
                paragraphs=paragraphs,
                output_dir=output_dir,
                work_root=work_root,
                speaker_id=speaker_id,
                max_chars=max_chars,
                pause_sec=pause_sec,
                use_accent=bool(accent),
            )
            chapter_elapsed = time.monotonic() - chapter_started
            _log("[{0}/{1}] runtime={2:.2f}s".format(i, len(chapters), chapter_elapsed))
    else:
        tasks = []
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_worker_init,
            initargs=(
                model_name,
                bool(accent),
                str(accent_model_size),
                bool(accent_use_dictionary),
                str(models_dir) if models_dir is not None else "",
            ),
        ) as ex:
            for i, (title, paragraphs) in enumerate(chapters, 1):
                fut = ex.submit(
                    _process_chapter_task,
                    i,
                    len(chapters),
                    title,
                    paragraphs,
                    str(output_dir),
                    str(work_root),
                    speaker_id,
                    max_chars,
                    pause_sec,
                    bool(accent),
                )
                tasks.append(fut)

            for fut in as_completed(tasks):
                idx, result_path, chapter_elapsed = fut.result()
                _log("[{0}/{1}] runtime={2:.2f}s -> {3}".format(idx, len(chapters), chapter_elapsed, Path(result_path).name))

    total_elapsed = time.monotonic() - total_started
    _log("all done; total_runtime={0:.2f}s".format(total_elapsed))

    return output_dir


def main() -> None:
    _load_dotenv_simple(_repo_root() / ".env")

    ap = argparse.ArgumentParser(description="Vosk TTS: FB2 -> MP3 (по главам)")
    ap.add_argument("input", help="Входной .fb2")
    ap.add_argument("-o", "--output-dir", help="Папка для MP3 глав")
    ap.add_argument("--model", default="vosk-model-tts-ru-0.9-multi", help="Название модели vosk-tts")
    ap.add_argument("--speaker-id", type=int, default=3, help="speaker_id (0..4)")
    ap.add_argument("--max-chars", type=int, default=250, help="Лимит символов на чанк")
    ap.add_argument("--pause-sec", type=float, default=0.03, help="Пауза между чанками")
    ap.add_argument("--workers", type=int, default=1, help="Количество параллельных воркеров по главам")
    ap.add_argument("--accent", action="store_true", help="Прогон текста через ruaccent (ставит '+' ударения)")
    ap.add_argument("--accent-model-size", default="turbo3.1", help="Размер омограф-модели ruaccent")
    ap.add_argument("--accent-use-dictionary", dest="accent_use_dictionary", action="store_true", help="ruaccent: использовать словарь (по умолчанию включен)")
    ap.add_argument("--accent-no-dictionary", dest="accent_use_dictionary", action="store_false", help="ruaccent: отключить словарь")
    ap.set_defaults(accent_use_dictionary=True)
    ap.add_argument("--accent-models-dir", help="Папка с моделями ruaccent; по умолчанию общий кэш пользователя")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.exists():
        sys.exit("Нет файла: {0}".format(inp))
    if inp.suffix.lower() != ".fb2":
        sys.exit("Ожидается .fb2")

    out_dir = Path(args.output_dir) if args.output_dir else inp.with_name("{0}_vosk_mp3".format(inp.stem))
    try:
        result = synthesize_fb2_to_mp3_chapters(
            fb2_path=inp,
            output_dir=out_dir,
            model_name=args.model,
            speaker_id=args.speaker_id,
            max_chars=args.max_chars,
            pause_sec=args.pause_sec,
            workers=args.workers,
            accent=bool(args.accent),
            accent_model_size=str(args.accent_model_size),
            accent_use_dictionary=bool(args.accent_use_dictionary),
            accent_models_dir=Path(args.accent_models_dir) if args.accent_models_dir else None,
        )
    except ET.ParseError as exc:
        sys.exit("Некорректный FB2/XML: {0}".format(exc))
    except Exception as exc:
        sys.exit("Ошибка: {0}".format(exc))

    print("Готово: {0}".format(result))


if __name__ == "__main__":
    main()
