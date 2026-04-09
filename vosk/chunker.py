"""Чанкер текста под vosk-tts."""

from __future__ import annotations

import re
from typing import List


def _compact_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


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
        else:
            for i in range(0, len(word), max_chars):
                out.append(word[i : i + max_chars])

    if buf:
        out.append(buf)
    return out


def _split_sentences_balanced(sentences: List[str], max_chars: int) -> List[str]:
    """
    Делит список предложений на примерно равные чанки (< max_chars).
    Предпочитает границы по предложениям.
    """
    units: List[str] = []
    for s in sentences:
        if len(s) <= max_chars:
            units.append(s)
        else:
            units.extend(_split_sentence_by_words(s, max_chars))

    if not units:
        return []
    if len(units) == 1:
        return units

    total_chars = sum(len(u) for u in units) + (len(units) - 1)
    chunks_needed = max(1, (total_chars + max_chars - 1) // max_chars)

    out: List[str] = []
    idx = 0
    remaining_chars = total_chars

    for chunk_num in range(chunks_needed):
        chunks_left = chunks_needed - chunk_num
        target = max(1, (remaining_chars + chunks_left - 1) // chunks_left)

        chunk_units: List[str] = []
        chunk_len = 0
        while idx < len(units):
            u = units[idx]
            add_len = len(u) if not chunk_units else len(u) + 1

            if chunk_units and chunk_len + add_len > max_chars:
                break

            # Добавляем, пока не приблизимся к целевому размеру.
            if chunk_units and chunk_len >= target:
                break

            chunk_units.append(u)
            chunk_len += add_len
            idx += 1

            # Если остался ровно один чанк, забираем остаток в него.
            if chunks_left == 1:
                continue

        if not chunk_units:
            # safety fallback
            chunk_units.append(units[idx])
            chunk_len = len(units[idx])
            idx += 1

        out.append(" ".join(chunk_units))
        remaining_chars -= chunk_len

    # хвост из-за округлений
    if idx < len(units):
        tail = " ".join(units[idx:])
        if len(tail) <= max_chars:
            out.append(tail)
        else:
            out.extend(_split_sentence_by_words(tail, max_chars))

    return out


def chunk_text_for_vosk(text: str, max_chars: int = 250) -> List[str]:
    """
    Режет текст на чанки:
    - сначала по предложениям;
    - если текст > max_chars, делит на примерно равные части;
    - границы старается ставить по целым предложениям;
    - слишком длинное предложение режет по словам.
    """
    if max_chars <= 0:
        raise ValueError("max_chars должен быть > 0")

    src = text.strip()
    if not src:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", src) if p.strip()]
    out: List[str] = []

    for paragraph in paragraphs:
        p = _compact_ws(paragraph)
        sentences = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", p) if s.strip()]
        if not sentences:
            out.extend(_split_sentence_by_words(p, max_chars))
            continue

        if len(p) <= max_chars:
            out.append(p)
            continue
        out.extend(_split_sentences_balanced(sentences, max_chars))

    return out
