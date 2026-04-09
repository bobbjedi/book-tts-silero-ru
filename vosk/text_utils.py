"""Текстовые утилиты для Vosk-пайплайна."""

from __future__ import annotations

import re

from book_tts.num_utils import int_to_words_ru


def replace_numbers_ru(text: str) -> str:
    """Заменяет целые числа в тексте на русские слова."""
    return re.sub(r"\d+", lambda m: int_to_words_ru(int(m.group(0))), text)

