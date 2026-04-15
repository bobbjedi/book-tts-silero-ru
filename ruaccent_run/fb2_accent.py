"""Прогон FB2 через ruaccent: расстановка ударений в формате '+' по главам.

Результат: папка с .txt по главам (и один общий файл).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from ruaccent import RUAccent
from huggingface_hub import snapshot_download


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


def _normalize_for_ruaccent(text: str) -> str:
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


def _accent_paragraphs(accentizer: RUAccent, paragraphs: List[str]) -> List[str]:
    out: List[str] = []
    for p in paragraphs:
        prepared = _normalize_for_ruaccent(p)
        if not prepared:
            continue
        out.append(accentizer.process_all(prepared))
    return out


def accent_fb2_to_txt_chapters(
    fb2_path: Path,
    output_dir: Path,
    *,
    overwrite: bool = False,
    save_cache: bool = True,
) -> Path:
    chapters = extract_fb2_chapters(fb2_path)
    if not chapters:
        raise ValueError("Не удалось извлечь главы из FB2")

    output_dir.mkdir(parents=True, exist_ok=True)
    work_root = output_dir / ".ruaccent_work"
    work_root.mkdir(parents=True, exist_ok=True)

    # Предскачиваем модели единым скачиванием: так есть прогресс и меньше шансов "зависнуть"
    # на множестве отдельных запросов/листингов.
    models_dir = output_dir / ".ruaccent_models"
    models_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id="ruaccent/accentuator",
        local_dir=str(models_dir),
        local_dir_use_symlinks=False,
        resume_download=True,
    )

    accentizer = RUAccent()
    accentizer.load(
        omograph_model_size="turbo3.1",
        use_dictionary=True,
        workdir=str(models_dir),
        repo="ruaccent/accentuator",
    )

    combined_path = output_dir / "_ALL.txt"
    combined_parts: List[str] = []

    _log("chapters={0}".format(len(chapters)))
    for idx, (title, paragraphs) in enumerate(chapters, 1):
        short_title = _short_chapter_title(title)
        base_name = _safe_name(short_title)
        out_txt = output_dir / "{0}.txt".format(base_name)

        chapter_key = hashlib.sha1((title + "\n" + "\n".join(paragraphs)).encode("utf-8")).hexdigest()[:16]
        chapter_work_dir = work_root / chapter_key
        chapter_work_dir.mkdir(parents=True, exist_ok=True)

        cache_path = chapter_work_dir / "accented.txt"
        meta_path = chapter_work_dir / "meta.json"

        if out_txt.exists() and out_txt.stat().st_size > 0 and not overwrite:
            _log("[{0}/{1}] skip existing {2}".format(idx, len(chapters), out_txt.name))
            combined_parts.append(out_txt.read_text(encoding="utf-8"))
            continue

        if cache_path.exists() and cache_path.stat().st_size > 0 and not overwrite:
            _log("[{0}/{1}] reuse cache {2}".format(idx, len(chapters), out_txt.name))
            accented_text = cache_path.read_text(encoding="utf-8")
            out_txt.write_text(accented_text, encoding="utf-8")
            combined_parts.append(accented_text)
            continue

        _log("[{0}/{1}] accent {2}".format(idx, len(chapters), base_name))
        accented_text = "\n".join(_accent_paragraphs(accentizer, paragraphs)).strip() + "\n"
        out_txt.write_text(accented_text, encoding="utf-8")
        combined_parts.append(accented_text)

        if save_cache:
            cache_path.write_text(accented_text, encoding="utf-8")
            meta_path.write_text(
                json.dumps(
                    {
                        "title": title,
                        "base_name": base_name,
                        "paragraphs": len(paragraphs),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

    combined_path.write_text("\n\n".join(x.strip() for x in combined_parts if x.strip()) + "\n", encoding="utf-8")
    _log("done: {0}".format(output_dir))
    return output_dir


def main() -> None:
    ap = argparse.ArgumentParser(description="RUAccent: FB2 -> accented TXT (по главам)")
    ap.add_argument("input", help="Входной .fb2")
    ap.add_argument("-o", "--output-dir", help="Папка для .txt глав")
    ap.add_argument("--overwrite", action="store_true", help="Перезаписать существующие файлы")
    ap.add_argument("--no-cache", action="store_true", help="Не сохранять кэш в .ruaccent_work")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.exists():
        sys.exit("Нет файла: {0}".format(inp))
    if inp.suffix.lower() != ".fb2":
        sys.exit("Ожидается .fb2")

    out_dir = Path(args.output_dir) if args.output_dir else inp.with_name("{0}_ruaccent_txt".format(inp.stem))
    try:
        accent_fb2_to_txt_chapters(
            fb2_path=inp,
            output_dir=out_dir,
            overwrite=bool(args.overwrite),
            save_cache=not bool(args.no_cache),
        )
    except ET.ParseError as exc:
        sys.exit("Некорректный FB2/XML: {0}".format(exc))
    except Exception as exc:
        sys.exit("Ошибка: {0}".format(exc))


if __name__ == "__main__":
    main()

