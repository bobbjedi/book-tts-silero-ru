# Book TTS (Silero)

Мини-пайплайн для озвучки книг:
- `book_tts/parser.py` — парсинг текста в чанки + генерация `*.parsed.json` и `*.chunks.txt`
- `book_tts/tts.py` — озвучка чанков в `wav` через Silero
- `book_tts/fb2_tts.py` — озвучка `.fb2` по главам в `mp3`

## Быстрый старт

### 1) Парсинг текста
```bash
python3 -m book_tts.parser tests/test.txt
```

Результат:
- `tests/test.parsed.json`
- `tests/test.chunks.txt`

### 2) Озвучка
```bash
python3 -m book_tts.tts tests/test.txt
```

Результат:
- `tests/test.wav`

## Полезные параметры

### Parser
```bash
python3 -m book_tts.parser <input.txt> --max-chars 850
```
- `--max-chars` — максимальная длина одного чанка (по умолчанию `850`)
- длинные чанки режутся в парсере (по предложениям, затем по словам)

### TTS
```bash
python3 -m book_tts.tts <input.txt|input.parsed.json> -o out.wav --pause-sec 0.25 --speaker xenia
```
- `--speaker` по умолчанию: `xenia`
- `--pause-sec` по умолчанию: `0.25`
- `--sample-rate` по умолчанию: `48000`
- `--model` по умолчанию: `v5_4_ru`

### FB2 -> chapter MP3
```bash
python3 -m book_tts.fb2_tts works/book.fb2
```

Результат:
- директория `works/book_mp3/`
- файлы вида `001_<название_главы>.mp3`, `002_<название_главы>.mp3`, ...

Опции:
```bash
python3 -m book_tts.fb2_tts works/book.fb2 -o works/output_mp3 --pause-sec 0.25 --speaker xenia --max-chars 850
```

## Примечания
- Для вопросов используется звездочная разметка вида `*слово*`.
- SSML строится в парсере и передается в Silero через `ssml_text`.
- Если входной файл `.txt`, модуль TTS сначала запускает парсер автоматически.
