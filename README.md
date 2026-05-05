# Book TTS (Silero)

Пайплайн для озвучки книг:
- `refactoring_text/txt_escaped_to_fb2.py` — конвертация `.txt` (в том числе с буквальными `\n`) в `.fb2` с `section` по главам.
- `book_tts/fb2_tts.py` — озвучка `.fb2` по главам в `.mp3`.
- `book_tts/tts.py` — озвучка обычного `.txt` в один `.wav`.

## Требования

- `python3`
- `ffmpeg`
- виртуальное окружение `.venv` с установленным `torch`

Проверка:
```bash
ffmpeg -version
.venv/bin/python -c "import torch; print(torch.__version__)"
```

## Быстрый запуск (txt -> fb2 -> mp3)

```bash
# 1) TXT -> FB2 с разбиением по главам (section)
python3 refactoring_text/txt_escaped_to_fb2.py \
  --in-txt "work/повелитель тайн.txt" \
  --out-fb2 "work/повелитель тайн.fb2" \
  --title "Повелитель тайн"

# 2) FB2 -> MP3 по главам голосом xenia
.venv/bin/python -u -m book_tts.fb2_tts \
  "work/повелитель тайн.fb2" \
  --speaker xenia \
  --model v5_5_ru
```

Результат:
- `work/повелитель тайн.fb2`
- папка `work/повелитель тайн_mp3/` с файлами глав (`Глава 1 ... .mp3`, `Глава 2 ... .mp3`, ...)

## Полезные команды

### TXT -> FB2
```bash
python3 refactoring_text/txt_escaped_to_fb2.py \
  --in-txt "work/book.txt" \
  --out-fb2 "work/book.fb2" \
  --title "Название книги"
```

### FB2 -> MP3 (по главам)
```bash
.venv/bin/python -u -m book_tts.fb2_tts "work/book.fb2" \
  -o "work/book_mp3" \
  --speaker xenia \
  --model v5_5_ru \
  --pause-sec 0.022 \
  --max-chars 850
```

Параметры:
- `--speaker` — голос Silero (например: `xenia`, `eugene`)
- `--model` — модель Silero (`v5_5_ru` по умолчанию в этом сценарии)
- `--pause-sec` — пауза между чанками
- `--max-chars` — максимальная длина чанка для парсера

### TXT -> WAV (одним файлом, без FB2)
```bash
.venv/bin/python -m book_tts.tts "tests/test.txt" -o "tests/test.wav" --speaker xenia
```

## Примечания

- Если озвучка прервалась, повторный запуск продолжит работу и пропустит уже готовые главы.
- Для стабильности запускай `fb2_tts` через `.venv/bin/python`, иначе может не найтись `torch`.
