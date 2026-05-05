#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple


FB2_NS = "http://www.gribuser.ru/xml/fictionbook/2.0"

CHAPTER_RE = re.compile(
    r"^(?:(Том)\s+(\d+)\s+)?(Глава)\s+(\d+)\s*(?:[-–—:]\s+(.+))?$"
)


def _fb(tag: str) -> str:
    return f"{{{FB2_NS}}}{tag}"


def _indent_xml(elem: ET.Element, level: int = 0) -> None:
    i = "\n" + level * "  "
    if len(elem):
        if not (elem.text or "").strip():
            elem.text = i + "  "
        for child in elem:
            _indent_xml(child, level + 1)
        if not (elem.tail or "").strip():
            elem.tail = i
    else:
        if level and not (elem.tail or "").strip():
            elem.tail = i


def _unescape_newlines(s: str) -> str:
    # Частый формат: одна строка, внутри встречаются буквальные "\n" и "\n\n"
    # Важно: обрабатываем именно обратный слэш + n, а не настоящие переводы строк.
    return s.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")


def _cleanup_wrappers(s: str) -> str:
    # Иногда встречаются лишние обёртки/символы в начале (например, обратная кавычка)
    s = s.lstrip("\ufeff")
    if s.startswith("`"):
        s = s[1:]
    return s.strip()


def _split_paragraphs(text: str) -> List[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # нормализуем “много пустых строк”
    text = re.sub(r"\n{3,}", "\n\n", text)
    parts = [p.strip() for p in text.split("\n\n")]
    return [p for p in parts if p]


def _chapter_heading_normalize(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    s = s.replace("—", "-").replace("–", "-")
    s = re.sub(r"\s*-\s*", " - ", s)
    return s.strip()


def _force_chapter_markers_to_paragraphs(text: str) -> str:
    """
    В исходнике маркеры глав часто “прилипают” к предыдущему абзацу:
    "... конец. Том 1 Глава 2 - ..."
    Стараемся вытащить маркер в отдельный абзац.
    """
    pattern = re.compile(
        r"(?<!\n)(\s+)((?:Том\s+\d+\s+)?Глава\s+\d+\s*(?:[-–—:]\s*)[^\n]{1,120})"
    )

    def repl(m: re.Match) -> str:
        return "\n\n" + m.group(2).strip() + "\n\n"

    text = pattern.sub(repl, text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _split_into_chapters(paragraphs: List[str], fallback_title: str) -> List[Tuple[str, List[str]]]:
    chapters: List[Tuple[str, List[str]]] = []
    cur_title = fallback_title
    cur_paras: List[str] = []

    for p in paragraphs:
        p1 = _chapter_heading_normalize(p)
        if CHAPTER_RE.match(p1):
            if cur_paras:
                chapters.append((cur_title, cur_paras))
            cur_title = p1
            cur_paras = []
            continue
        cur_paras.append(p)

    if cur_paras or not chapters:
        chapters.append((cur_title, cur_paras))
    return chapters


def _guess_title(paragraphs: List[str], default: str) -> str:
    if not paragraphs:
        return default
    first = paragraphs[0]
    # если первая строка выглядит как заголовок (короткая) — берём её
    first_line = first.split("\n", 1)[0].strip()
    if 2 <= len(first_line) <= 120:
        return first_line
    return default


@dataclass(frozen=True)
class Meta:
    title: str
    author: Optional[Tuple[str, str]]  # first, last


def _build_fb2(chapters: List[Tuple[str, List[str]]], meta: Meta) -> ET.ElementTree:
    ET.register_namespace("", FB2_NS)

    fb = ET.Element(_fb("FictionBook"))

    desc = ET.SubElement(fb, _fb("description"))
    title_info = ET.SubElement(desc, _fb("title-info"))
    if meta.author is not None:
        a = ET.SubElement(title_info, _fb("author"))
        ET.SubElement(a, _fb("first-name")).text = meta.author[0]
        ET.SubElement(a, _fb("last-name")).text = meta.author[1]
    ET.SubElement(title_info, _fb("book-title")).text = meta.title
    ET.SubElement(title_info, _fb("lang")).text = "ru"

    doc_info = ET.SubElement(desc, _fb("document-info"))
    ET.SubElement(doc_info, _fb("program-used")).text = "txt_escaped_to_fb2.py"
    ET.SubElement(doc_info, _fb("date"), {"value": datetime.now().strftime("%Y-%m-%d")}).text = datetime.now().strftime(
        "%Y-%m-%d"
    )

    body = ET.SubElement(fb, _fb("body"))
    for ch_title, ch_paras in chapters:
        section = ET.SubElement(body, _fb("section"))
        title = ET.SubElement(section, _fb("title"))
        ET.SubElement(title, _fb("p")).text = ch_title

        for para in ch_paras:
            cleaned = re.sub(r"\s*\n\s*", " ", para).strip()
            if cleaned:
                ET.SubElement(section, _fb("p")).text = cleaned

    _indent_xml(fb)
    return ET.ElementTree(fb)


def main() -> int:
    ap = argparse.ArgumentParser(description="Convert text with literal \\\\n sequences into FB2.")
    ap.add_argument("--in-txt", required=True, type=Path, help="Input .txt")
    ap.add_argument("--out-fb2", required=True, type=Path, help="Output .fb2")
    ap.add_argument("--title", default=None, help="Override title")
    ap.add_argument("--author-first", default=None, help="Author first name")
    ap.add_argument("--author-last", default=None, help="Author last name")
    args = ap.parse_args()

    raw = args.in_txt.read_text(encoding="utf-8", errors="replace")
    raw = _cleanup_wrappers(raw)
    text = _unescape_newlines(raw)
    text = _force_chapter_markers_to_paragraphs(text)
    paragraphs = _split_paragraphs(text)

    title_default = args.in_txt.stem
    title = args.title or _guess_title(paragraphs, default=title_default)
    author = None
    if args.author_first and args.author_last:
        author = (args.author_first, args.author_last)

    chapters = _split_into_chapters(paragraphs, fallback_title=title)
    if chapters:
        t0, ps0 = chapters[0]
        if ps0 and _chapter_heading_normalize(ps0[0]) == _chapter_heading_normalize(t0):
            chapters[0] = (t0, ps0[1:])

    tree = _build_fb2(chapters, Meta(title=title, author=author))
    args.out_fb2.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(args.out_fb2), encoding="utf-8", xml_declaration=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

