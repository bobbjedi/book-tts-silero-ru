# Vosk TTS (RU)

Короткий гайд для `vosk.fb2_synthesize` и `vosk.synthesize`.

## Зависимости

- `python3`
- `ffmpeg`
- установленный `vosk_tts`
- для акцента: `ruaccent`

Проверка:
```bash
ffmpeg -version
```

## 1) TXT -> WAV

```bash
python3 -m vosk.synthesize tests/test.txt \
  --speaker-id 3 \
  --pause-sec 0.03 \
  --max-chars 250
```

Результат по умолчанию: `tests/test.vosk.wav`.

## 2) FB2 -> MP3 по главам

```bash
python3 -m vosk.fb2_synthesize "work/WTC 6_7.fb2" \
  -o "work/WTC 6_7_vosk_mp3" \
  --speaker-id 3 \
  --pause-sec 0.03 \
  --max-chars 250 \
  --workers 1
```

## 3) FB2 + ruaccent

```bash
python3 -m vosk.fb2_synthesize "work/WTC 6_7.fb2" \
  --accent \
  --accent-model-size turbo3.1
```

Опции `ruaccent`:
- `--accent` — включить расстановку `+` ударений перед синтезом.
- `--accent-model-size` — размер омограф-модели.
- `--accent-use-dictionary` — использовать словарь (по умолчанию включен).
- `--accent-no-dictionary` — отключить словарь.
- `--accent-models-dir` — папка моделей; если не задана, используется общий кэш пользователя.

## 4) Запуск с логом

```bash
python3 -m vosk.fb2_synthesize "work/WTC 6_7.fb2" --accent 2>&1 | tee "work/WTC 6_7_accent_vosk.log"
```

## 5) Resume / кэш

- Готовая глава (`*.mp3`) пропускается.
- Промежуточные данные: `OUTPUT/.vosk_fb2_work/`.
- Для главы сохраняются `chunks.json`, `chunks.txt`, `state.json`.
- При перезапуске части `part_XXXXX.wav` переиспользуются.
- При сбое чанка создается `part_XXXXX.skip`.

## 6) Важные флаги

- `--model` (по умолчанию `vosk-model-tts-ru-0.9-multi`)
- `--speaker-id` (по умолчанию `3`)
- `--max-chars` (по умолчанию `250`)
- `--pause-sec` (по умолчанию `0.03`)
- `--workers` (по умолчанию `1`)
