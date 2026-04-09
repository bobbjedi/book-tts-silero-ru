#!/usr/bin/env python3
"""Читает .txt: перед каждым «?» оборачивает *каждое* слово в *...*."""

import argparse
import re
import sys
from pathlib import Path

_WORD = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+(?:-[A-Za-zА-Яа-яЁё0-9]+)*")


def wrap_words_in_fragment(fragment: str) -> str:
    return _WORD.sub(lambda m: f"*{m.group(0)}*", fragment)


def wrap_each_word_before_question(text: str) -> str:
    """
    Для каждого «?» во фрагменте текста до него все слова — в *слово*.
    Если во фрагменте уже есть «*», не меняем (ручная разметка).
    """
    if "*" in text:
        return text
    parts: list[str] = []
    rest = text
    while "?" in rest:
        i = rest.index("?")
        before = rest[:i]
        rest = rest[i + 1 :]
        parts.append(wrap_words_in_fragment(before) + "?")
    parts.append(rest)
    return "".join(parts)


def focus(text: str) -> str:
    """По абзацам: там, где нет *, — оборачиваем слова перед ?."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    out: list[str] = []
    for p in paras:
        out.append(wrap_each_word_before_question(p) if "*" not in p else p)
    return "\n\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="*каждое* *слово* перед ?")
    ap.add_argument("input", help="Входной .txt")
    ap.add_argument("-o", "--output", help="Выход (по умолчанию stdout)")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.exists():
        sys.exit(f"Нет файла: {inp}")
    out = focus(inp.read_text(encoding="utf-8"))

    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
    else:
        sys.stdout.write(out)


if __name__ == "__main__":
    main()
