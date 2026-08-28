# Как добавлять новые навыки

## Новый источник данных

1. Добавить переменные в `.env.example`.
2. Добавить функцию чтения источника в `scripts/tg_bot_agent.py`.
3. Добавить команду или естественный маршрут в `COMMANDS` / `resolve_digest_id`.
4. Добавить формат ответа: период, источник, ссылка, свежесть данных.
5. Добавить self-test в `run_self_tests`.

## Новая команда

Минимальный путь:

1. Добавить фразу в `COMMANDS`.
2. Добавить обработку `digest_id` в `build_digest_message`.
3. Проверить через `preview`:

```bash
python3 scripts/tg_bot_agent.py --config data/tg_bot_agent.config.example.json preview "текст команды"
```

## Новый регулярный сценарий

Использовать `/schedule_group` для Telegram-сценариев или добавить новый digest в `data/bot_alert_config.example.json`.

## Новый язык

1. Расширить `detect_text_language`.
2. Добавить словарь коротких шаблонов в `localize_answer_for_text`.
3. Добавить self-test на вопрос и ответ.
