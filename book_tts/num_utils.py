"""Утилиты для преобразования чисел в русские слова."""

from __future__ import annotations

import re
from typing import List, Tuple


def int_to_words_ru(number: int) -> str:
    if number == 0:
        return "ноль"
    if number < 0:
        return "минус {0}".format(int_to_words_ru(abs(number)))

    units_m = ("", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять")
    units_f = ("", "одна", "две", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять")
    teens = (
        "десять",
        "одиннадцать",
        "двенадцать",
        "тринадцать",
        "четырнадцать",
        "пятнадцать",
        "шестнадцать",
        "семнадцать",
        "восемнадцать",
        "девятнадцать",
    )
    tens = ("", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто")
    hundreds = ("", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот")
    scales = [
        (("", "", ""), False),
        (("тысяча", "тысячи", "тысяч"), True),
        (("миллион", "миллиона", "миллионов"), False),
        (("миллиард", "миллиарда", "миллиардов"), False),
    ]

    def choose_form(n: int, forms: Tuple[str, str, str]) -> str:
        n_mod100 = n % 100
        n_mod10 = n % 10
        if 11 <= n_mod100 <= 14:
            return forms[2]
        if n_mod10 == 1:
            return forms[0]
        if 2 <= n_mod10 <= 4:
            return forms[1]
        return forms[2]

    def triad_to_words(n: int, feminine: bool) -> List[str]:
        words: List[str] = []
        words.append(hundreds[n // 100])
        last_two = n % 100
        if 10 <= last_two <= 19:
            words.append(teens[last_two - 10])
        else:
            words.append(tens[last_two // 10])
            unit = last_two % 10
            words.append((units_f if feminine else units_m)[unit])
        return [w for w in words if w]

    triads: List[int] = []
    n = number
    while n > 0:
        triads.append(n % 1000)
        n //= 1000

    words: List[str] = []
    for idx in range(len(triads) - 1, -1, -1):
        triad = triads[idx]
        if triad == 0:
            continue
        forms, feminine = scales[idx] if idx < len(scales) else (("", "", ""), False)
        words.extend(triad_to_words(triad, feminine))
        if forms[0]:
            words.append(choose_form(triad, forms))
    return " ".join(words)


def replace_numbers_ru(text: str) -> str:
    return re.sub(r"\d+", lambda m: int_to_words_ru(int(m.group(0))), text)


def normalize_yo_to_e(text: str) -> str:
    return text.replace("Ё", "Е").replace("ё", "е")
