# Vosk TTS (RU)

Минимальный набор команд для ветки `vosk`.

## 1) TXT -> WAV

```bash
python3 -m vosk.synthesize tests/test.txt \
  --speaker-id 4 \
  --pause-sec 0.025 \
  --max-chars 300
```

По умолчанию выход: `tests/test.vosk.wav`.

## 2) FB2 -> MP3 (по главам)

```bash
python3 -m vosk.fb2_synthesize "works/Стоит свеч 6 том.fb2" \
  -o "works/Стоит_свеч_6_том_vosk_mp3" \
  --speaker-id 4 \
  --pause-sec 0.025 \
  --max-chars 300 \
  --workers 2
```

Что делает:
- берёт `<section>` как главу;
- внутри главы использует абзацы `<p>`;
- перед чанкингом заменяет цифры на слова и `ё -> е`;
- если абзац `<= max_chars` — отдаёт как есть;
- если абзац `> max_chars` — делит на примерно равные куски `< max_chars` по целым предложениям;
- собирает WAV и конвертирует в MP3.

## 3) Resume и пропуски

- Если `*.mp3` главы уже есть, глава пропускается.
- Временные данные хранятся в `OUTPUT/.vosk_fb2_work/`.
- Для каждой главы сохраняются `chunks.json` и `chunks.txt`; при рестарте они подхватываются.
- Если чанк не удалось озвучить, рядом пишется `part_XXXXX.skip` с `reason` и `chunk_text`.

## 4) Полезные флаги

- `--speaker-id 4` — мужской голос (дефолт).
- `--pause-sec 0.025` — пауза между чанками.
- `--max-chars 300` — лимит символов чанка.
- `--workers 2` — параллельная обработка глав (если хватает CPU/RAM).
- `--model vosk-model-tts-ru-0.9-multi` — модель vosk-tts.
