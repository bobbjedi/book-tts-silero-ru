"""Парсер художественного текста в чанки для TTS."""

from __future__ import annotations

import argparse
import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

from .num_utils import normalize_yo_to_e, replace_numbers_ru

ChunkType = Literal["author", "line", "question", "exclamation"]

# Поддерживаемые значения Silero SSML (prosody).
SUPPORTED_RATE_VALUES = ("x-slow", "slow", "medium", "fast", "x-fast")
SUPPORTED_PITCH_VALUES = ("x-low", "low", "medium", "high", "x-high")
DEFAULT_MAX_CHARS = 850

DEFAULT_PROFILES: Dict[ChunkType, Dict[str, str]] = {
    "line": {"pitch": "high", "rate": "medium"},
    "exclamation": {"pitch": "x-high", "rate": "medium"},
    "question": {"pitch": "high", "rate": "medium"},
    "author": {},
}


@dataclass
class Chunk:
    type: ChunkType
    text: str
    ssml: Optional[str] = None

    def to_dict(self) -> Dict[str, str]:
        data = {"type": self.type, "text": self.text}
        if self.ssml is not None:
            data["ssml"] = self.ssml
        return data


def parse_book_text(
    text: str,
    profiles: Optional[Dict[ChunkType, Dict[str, str]]] = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> List[Chunk]:
    """Разбирает текст книги в последовательность чанков."""
    cfg = profiles or DEFAULT_PROFILES
    prepared = _replace_numbers(_normalize_global(text))
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", prepared) if p.strip()]

    chunks: List[Chunk] = []
    for paragraph in paragraphs:
        chunks.extend(_parse_paragraph(paragraph, cfg))
    return _enforce_max_chunk_size(chunks, cfg, max_chars)


def parse_text_file(
    input_path: str,
    profiles: Optional[Dict[ChunkType, Dict[str, str]]] = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> Tuple[Path, Path]:
    """
    Парсит .txt и ОБЯЗАТЕЛЬНО пишет 2 файла рядом:
    - <name>.parsed.json
    - <name>.chunks.txt
    """
    src = Path(input_path)
    text = src.read_text(encoding="utf-8")
    chunks = parse_book_text(text, profiles=profiles, max_chars=max_chars)

    json_path = src.with_name("{0}.parsed.json".format(src.stem))
    chunks_path = src.with_name("{0}.chunks.txt".format(src.stem))

    payload = [chunk.to_dict() for chunk in chunks]
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["[{0}]: {1}".format(chunk.type, chunk.text) for chunk in chunks]
    chunks_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, chunks_path


def _parse_paragraph(
    paragraph: str,
    profiles: Dict[ChunkType, Dict[str, str]],
) -> List[Chunk]:
    p = _compact_ws(paragraph)
    if not p.startswith("-"):
        return [_make_author_chunk(p)]

    body = p[1:].strip()
    parts = [part.strip() for part in re.split(r"\s+-\s+", body) if part.strip()]

    chunks: List[Chunk] = []
    speech_buffer: List[str] = []
    for part in parts:
        if _looks_like_author_remark(part):
            if speech_buffer:
                chunks.extend(_speech_to_chunks(" - ".join(speech_buffer), profiles))
                speech_buffer = []
            chunks.append(_make_author_chunk(part))
        else:
            speech_buffer.append(part)

    if speech_buffer:
        chunks.extend(_speech_to_chunks(" - ".join(speech_buffer), profiles))
    return chunks


def _speech_to_chunks(
    text: str,
    profiles: Dict[ChunkType, Dict[str, str]],
) -> List[Chunk]:
    normalized = _ensure_terminal_punctuation(_compact_ws(text))
    sentences = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", normalized) if s.strip()]

    out: List[Chunk] = []
    for sentence in sentences:
        sentence = _ensure_terminal_punctuation(_capitalize_first(sentence))
        kind = _detect_kind(sentence)
        if kind == "question":
            sentence = _mark_last_word_in_question(sentence)
        # Склеиваем подряд идущие line/exclamation в один чанк.
        if out and out[-1].type == kind and kind != "question":
            merged_text = "{0} {1}".format(out[-1].text, sentence)
            out[-1].text = merged_text
            if kind == "question":
                out[-1].ssml = _build_plain_ssml(merged_text)
            else:
                out[-1].ssml = _build_ssml(merged_text, profiles[kind])
            continue
        if kind == "question":
            ssml = _build_plain_ssml(sentence)
        else:
            ssml = _build_ssml(sentence, profiles[kind])
        out.append(Chunk(type=kind, text=sentence, ssml=ssml))
    return out


def _enforce_max_chunk_size(
    chunks: List[Chunk],
    profiles: Dict[ChunkType, Dict[str, str]],
    max_chars: int,
) -> List[Chunk]:
    if max_chars <= 0:
        return chunks

    out: List[Chunk] = []
    for chunk in chunks:
        if len(chunk.text) <= max_chars:
            out.append(chunk)
            continue
        out.extend(_split_chunk(chunk, profiles, max_chars))
    return out


def _split_chunk(
    chunk: Chunk,
    profiles: Dict[ChunkType, Dict[str, str]],
    max_chars: int,
) -> List[Chunk]:
    pieces = _split_text_to_max(chunk.text, max_chars)
    if len(pieces) <= 1:
        return [chunk]

    if chunk.type == "author":
        return [_make_author_chunk(piece) for piece in pieces]

    split_result: List[Chunk] = []
    for piece in pieces:
        split_result.extend(_speech_to_chunks(piece, profiles))
    return split_result


def _split_text_to_max(text: str, max_chars: int) -> List[str]:
    normalized = _compact_ws(text)
    if len(normalized) <= max_chars:
        return [normalized]

    sentences = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", normalized) if s.strip()]
    if not sentences:
        return _split_sentence_by_words(normalized, max_chars)

    pieces: List[str] = []
    buf = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            if buf:
                pieces.append(buf)
                buf = ""
            pieces.extend(_split_sentence_by_words(sentence, max_chars))
            continue

        candidate = sentence if not buf else "{0} {1}".format(buf, sentence)
        if len(candidate) <= max_chars:
            buf = candidate
        else:
            pieces.append(buf)
            buf = sentence

    if buf:
        pieces.append(buf)
    return pieces


def _split_sentence_by_words(sentence: str, max_chars: int) -> List[str]:
    words = sentence.split()
    if not words:
        return []

    out: List[str] = []
    buf = ""
    for word in words:
        candidate = word if not buf else "{0} {1}".format(buf, word)
        if len(candidate) <= max_chars:
            buf = candidate
            continue

        if buf:
            out.append(buf)
            buf = ""

        if len(word) <= max_chars:
            buf = word
            continue

        for i in range(0, len(word), max_chars):
            out.append(word[i : i + max_chars])

    if buf:
        out.append(buf)
    return out


def _looks_like_author_remark(text: str) -> bool:
    t = _compact_ws(text).lower()
    verb_core = (
        r"сказал(?:а|о|и)?|"
        r"спросил(?:а|о|и)?|"
        r"ответил(?:а|о|и)?|"
        r"заметил(?:а|о|и)?|"
        r"добавил(?:а|о|и)?|"
        r"произнес(?:ла|ли)?|произн[её]с(?:ла|ли)?|"
        r"прошептал(?:а|о|и)?|"
        r"крикнул(?:а|о|и)?|"
        r"раздал(?:ся|ась|ось|ись)|"
        r"ухмыльнул(?:ся|ась|ись)|"
        r"улыбнул(?:ся|ась|ись)|"
        r"пожал(?:а|о|и)?|"
        r"кивнул(?:а|о|и)?|"
        r"вздохнул(?:а|о|и)?|"
        r"отступил(?:а|о|и)?|"
        r"схватил(?:а|о|и)?"
    )

    # Слова автора в диалоге обычно не заканчиваются ?/! (кроме редких случаев).
    if "?" in t or "!" in t:
        return bool(
            re.match(
                r"^\s*(?:(?:вдруг|тихо|медленно|резко)\s+)?"
                r"(?:"
                + verb_core
                + r")\b",
                t,
            )
        )

    return bool(
        re.match(
            r"^\s*(?:(?:она|он|они|я|мы|ты|вы|[а-яёa-z][а-яёa-z-]+)\s+)?"
            r"(?:(?:вдруг|тихо|медленно|резко)\s+)?"
            r"(?:"
            + verb_core
            + r")\b",
            t,
        )
    )


def _make_author_chunk(text: str) -> Chunk:
    normalized = _ensure_terminal_punctuation(_capitalize_first(_compact_ws(text)))
    return Chunk(type="author", text=normalized, ssml=None)


def _normalize_global(text: str) -> str:
    text = normalize_yo_to_e(text)
    text = text.replace("—", "-").replace("–", "-")
    text = re.sub(r"\?!|!\?", "?", text)
    return text


def _compact_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _ensure_terminal_punctuation(text: str) -> str:
    if re.search(r"[,:;]$", text):
        return re.sub(r"[,:;]$", ".", text)
    return text if re.search(r"[.!?…]$", text) else f"{text}."


def _capitalize_first(text: str) -> str:
    if not text:
        return text
    return text[0].upper() + text[1:]


def _detect_kind(sentence: str) -> ChunkType:
    if sentence.endswith("?"):
        return "question"
    if sentence.endswith("!"):
        return "exclamation"
    return "line"


def _mark_last_word_in_question(text: str) -> str:
    if not text.endswith("?"):
        return text
    body = text[:-1].rstrip()
    match = re.search(r"([A-Za-zА-Яа-яЁё0-9-]+)$", body)
    if not match:
        return text
    start, end = match.span(1)
    marked = f"{body[:start]}*{body[start:end]}*"
    return f"{marked}?"


def _build_ssml(text: str, profile: Dict[str, str]) -> str:
    escaped = html.escape(text, quote=False)
    pitch = profile.get("pitch", "medium")
    rate = profile.get("rate", "medium")
    return (
        "<speak><p>"
        f"<prosody pitch=\"{pitch}\" rate=\"{rate}\">{escaped}</prosody>"
        "</p></speak>"
    )


def _build_plain_ssml(text: str) -> str:
    escaped = html.escape(text, quote=False)
    return "<speak><p>{0}</p></speak>".format(escaped)


def _replace_numbers(text: str) -> str:
    return replace_numbers_ru(text)


def main() -> None:
    ap = argparse.ArgumentParser(description="Парсер книги в JSON + chunks debug")
    ap.add_argument("input", help="Входной .txt файл")
    ap.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="Макс. длина чанка в символах")
    args = ap.parse_args()

    json_path, chunks_path = parse_text_file(args.input, max_chars=args.max_chars)
    print(json_path)
    print(chunks_path)


if __name__ == "__main__":
    main()
