# Book TTS (Silero)

Мини-пайплайн для озвучки книг:
- `book_tts/parser.py` — парсинг текста в чанки + генерация `*.parsed.json` и `*.chunks.txt`
- `book_tts/tts.py` — озвучка чанков в `wav` через Silero
- `book_tts/fb2_tts.py` — озвучка `.fb2` по главам в `mp3`

## Запуск за 1 минуту

```bash
# 1) TXT -> WAV
python3 -m book_tts.tts tests/test.txt

# 2) FB2 -> MP3 по главам
python3 -u -m book_tts.fb2_tts "works/Незнайка на Луне.fb2"
```

Где искать результат:
- TXT: `tests/test.wav`
- FB2: папка `works/Незнайка на Луне/`

## Что нужно для работы

- `python3`
- `ffmpeg` (обязательно, используется для склейки и mp3)
- PyTorch + зависимости Silero (если уже запускал скрипты, скорее всего все есть)

Проверка `ffmpeg`:
```bash
ffmpeg -version
```

## Подробно

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

#### Профили голоса (`pitch` / `rate` / `post_tone`)
В `book_tts/parser.py` задаётся `DEFAULT_PROFILES` для типов чанков: `author`, `line`, `question`, `exclamation`.
- `pitch`, `rate` — значения Silero SSML (`prosody`). Если **оба** ключа отсутствуют или оба пустые строки, в Silero передаётся **обычный текст без SSML** (можно оставить только `post_tone` и т.п.).
- **`post_tone`** (опционально) — после синтеза каждый WAV-чанк прогоняется через **ffmpeg** (`asetrate` + `atempo`): подстройка **высоты записанного звука**, без намёка на SSML-«pitch». Удобно слегка «отстранить» авторский голос, не включая `pitch="low"` (у Silero он часто звучит вяло).

Формат строки:
- проценты: `"-3%"`, `"+2%"` — множитель высоты \(1 + p/100\);
- герцы: `"-5hz"` — сдвиг относительно условного F0 **200 Hz** (см. `book_tts/audio_pitch.py`, константа `DEFAULT_REF_F0_HZ`): множитель \((200 + \Delta)/(200)\).

Поле в `*.parsed.json` — **`post_tone`**. Старое имя **`pitch_shift`** в JSON и в кастомных профилях по-прежнему читается (обратная совместимость).

### TTS
```bash
python3 -m book_tts.tts <input.txt|input.parsed.json> -o out.wav --pause-sec 0.022 --speaker xenia
```
- `--speaker` по умолчанию: `xenia`
- `--pause-sec` по умолчанию: `0.022` (22 мс)
- `--sample-rate` по умолчанию: `48000`
- `--model` по умолчанию: `v5_4_ru`

### FB2 -> chapter MP3
```bash
python3 -m book_tts.fb2_tts works/book.fb2
```

Результат:
- директория `works/book_mp3/`
- файлы вида `Глава 106 ... .mp3`, `Глава 107 ... .mp3`, ...

Опции:
```bash
python3 -m book_tts.fb2_tts works/book.fb2 -o works/output_mp3 --pause-sec 0.022 --speaker xenia --max-chars 850
```

## Примечания
- Для вопросов используется звездочная разметка вида `*слово*`.
- SSML строится в парсере и передается в Silero через `ssml_text`.
- Если входной файл `.txt`, модуль TTS сначала запускает парсер автоматически.
- Постобработка `post_tone` требует **ffmpeg** (как и склейка WAV).
