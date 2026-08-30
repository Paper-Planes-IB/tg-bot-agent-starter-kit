# Передача TG KD Agent Bot в работу Claude Фатхулло

Этот документ нужен, чтобы новый разработчик или Claude Фатхулло мог продолжить работу с текущим Telegram-агентом без потери контекста.

## 1. Что это за бот

Бот: `@TGkdagent_bot`

Назначение:

- читать разрешённые Telegram-группы;
- читать личные чаты через Telegram Business / Secretary Mode;
- собирать интерпретированные сводки по группам и личным чатам;
- отвечать на вопросы по управленческому дашборду;
- работать с задачами ClickUp из списка `ОГ`;
- фиксировать поручения, вопросы, решения, риски и напоминания;
- отправлять сообщения в группы;
- отправлять регулярные сводки;
- отвечать на русском или узбекском в зависимости от языка переписки.

## 2. Где код

Публичный репозиторий:

```text
https://github.com/Paper-Planes-IB/tg-bot-agent-starter-kit
```

Локальная копия starter kit:

```text
/Users/natalie/Library/CloudStorage/GoogleDrive-tokaeva@paper-planes.ru/Shared drives/Paper Planes/4. Производство/RG2 Vault/70_Activities/70.1_Engagements/70.1.2_RG2/Toshkent Gullari/03_Артефакты/tg-bot-agent-starter-kit-2026-08-28
```

Live-код, который сейчас обслуживает настоящего бота:

```text
/Users/natalie/.local/share/tg-dashboard-agent-runtime/scripts/tg_dashboard_agent.py
```

Live-env с настоящими доступами:

```text
/Users/natalie/.local/share/tg-dashboard-agent-runtime/deployment-access/tg-dashboard-agent.env
```

Файл содержит live-токены и доступы. Его нельзя коммитить, отправлять в открытый чат или вставлять в промпт модели.

## 3. Как сейчас запущен live-бот

Бот запущен локально через macOS launchd:

```text
~/Library/LaunchAgents/com.paperplanes.tg-kd-agent.plist
```

Проверка статуса:

```bash
set -a; source "$HOME/.local/share/tg-dashboard-agent-runtime/deployment-access/tg-dashboard-agent.env"; set +a
python3 "$HOME/.local/share/tg-dashboard-agent-runtime/scripts/tg_dashboard_agent.py" \
  --config "$HOME/.local/share/tg-dashboard-agent-runtime/data/tg_dashboard_agent.config.example.json" \
  live-status
```

Перезапуск:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.paperplanes.tg-kd-agent.plist 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.paperplanes.tg-kd-agent.plist
```

## 4. Что нельзя делать

- Нельзя печатать значения `.env` в чат.
- Нельзя коммитить `.env`, токены, историю чатов, выгрузки клиента.
- Нельзя запускать второй polling-процесс с тем же Telegram token, пока live-процесс работает.
- Нельзя менять guard на открытый доступ без явного решения владельца.
- Нельзя удалять локальные JSONL-журналы без бэкапа.

## 5. Как вносить изменения

Рабочий порядок:

1. Найти нужную функцию в `scripts/tg_bot_agent.py` в starter kit.
2. Внести правку в starter kit.
3. Прогнать проверки:

```bash
python3 scripts/tg_bot_agent.py --config data/tg_bot_agent.config.example.json self-test
python3 -m py_compile scripts/tg_bot_agent.py
```

4. Перенести такую же правку в live-файл:

```text
/Users/natalie/.local/share/tg-dashboard-agent-runtime/scripts/tg_dashboard_agent.py
```

5. Прогнать live-проверки:

```bash
set -a; source "$HOME/.local/share/tg-dashboard-agent-runtime/deployment-access/tg-dashboard-agent.env"; set +a
python3 "$HOME/.local/share/tg-dashboard-agent-runtime/scripts/tg_dashboard_agent.py" \
  --config "$HOME/.local/share/tg-dashboard-agent-runtime/data/tg_dashboard_agent.config.example.json" \
  self-test

python3 "$HOME/.local/share/tg-dashboard-agent-runtime/scripts/tg_dashboard_agent.py" \
  --config "$HOME/.local/share/tg-dashboard-agent-runtime/data/tg_dashboard_agent.config.example.json" \
  doctor
```

6. Перезапустить launchd.
7. Проверить в Telegram.
8. Закоммитить и запушить starter kit.

## 6. Минимальный smoke test в Telegram

Отправить боту:

```text
/doctor
Какие чаты есть у меня
А какие есть личные сообщения
что важного в чатах
какая выручка за вчера
задачи по мне
```

Ожидаемое поведение:

- `/doctor` показывает `READY`;
- список чатов показывает известные группы;
- личные сообщения показывают Telegram Business-сводку;
- групповые сводки начинаются с приоритетных групп;
- если в переписке есть узбекский, сводка пишется на узбекском;
- запрос по выручке показывает период, сумму, источник, ссылку на дашборд и свежесть данных.

## 7. Текущие приоритеты сводок

Роли, которые бот учитывает в сводках:

- Мухрим Абдулазизов — CEO. Его нельзя автоматически ставить операционным исполнителем; к нему поднимать стратегические вопросы, эскалации, бюджет, приоритеты и финальные решения.
- Мубарак, Абдуазиз и Махинур — соучредители, примерно сопоставимые по статусу.
- Абдулазиз и Мухаммаджон — участники опергруппы наравне с Фатхулло.
- Фатхулло — получатель сводок и участник опергруппы; задачи для него формулировать как операционные следующие шаги, если это следует из переписки.
- По другим сотрудникам Ташкент Флоры бот ориентируется по контексту переписки.

Приоритетные личные чаты:

```text
Мухрим Абдулазизов, Муборак, Мохинул, Абдуазиз
```

Приоритетные группы:

```text
TG-PP / TG+PP, Опер группа / ОГ
```

В live-env это задано так:

```bash
TG_AGENT_PRIORITY_BUSINESS_CHATS='Мухрим Абдулазизов,Муборак,Мохинул,Абдуазиз'
TG_AGENT_PRIORITY_GROUP_CHATS='TG-PP,TG+PP,Опер группа,ОГ'
```

## 8. Регулярные сводки

Сводки зафиксированы в:

```text
/Users/natalie/.local/share/tg-dashboard-agent-runtime/data/tg_agent_reminders.jsonl
```

Текущее расписание:

- каждый день `09:30` — `/group_important`;
- каждый день `09:30` — `/business_summary`;
- каждый день `12:30` — `/business_summary`;
- каждый день `15:30` — `/business_summary`;
- каждый день `18:30` — `/business_summary`;
- каждый день `21:30` — `/business_summary`;
- каждый день `00:00` — `/business_summary`.

Как считаются периоды:

- регулярная сводка всегда показывает точное окно в строке `Период`;
- начало окна — конец предыдущего выпуска такого же типа сводки в этот же чат;
- конец окна — текущее scheduled-время;
- сообщения на границе прошлого окна не попадают во второй раз;
- если бот проснулся позже расписания, окно всё равно считается по scheduled-времени, а не по времени пробуждения компьютера.

Примеры:

- `12:30` по личным чатам: `09:30 - 12:30`;
- `15:30` по личным чатам: `12:30 - 15:30`;
- утренняя групповая сводка `09:30`: от предыдущих `09:30` до текущих `09:30`;
- утренняя личная сводка `09:30`: от ночного выпуска `00:00` до `09:30`.

Получатель: личный чат Фатхулло.

Проверить расписание:

```bash
set -a; source "$HOME/.local/share/tg-dashboard-agent-runtime/deployment-access/tg-dashboard-agent.env"; set +a
python3 "$HOME/.local/share/tg-dashboard-agent-runtime/scripts/tg_dashboard_agent.py" \
  --config "$HOME/.local/share/tg-dashboard-agent-runtime/data/tg_dashboard_agent.config.example.json" \
  preview "/reminders"
```

## 9. Telegram Business / личные чаты

Для чтения личных чатов бот использует Telegram Business / Secretary Mode.

При настройке компьютера Фатхулло отдельно спросить у него:

```text
Какие личные, нерабочие контакты нужно считать важными для сводок?
```

Наталья не знает этот список, поэтому его нельзя придумывать заранее.

Важные переменные:

```bash
TG_AGENT_ALLOWED_BUSINESS_CONNECTION_IDS=...
```

Если личная сводка пустая:

1. Проверить, включён ли Secretary Mode в BotFather.
2. Проверить, подключён ли бот в Telegram Business у владельца аккаунта.
3. Проверить, разрешено ли чтение сообщений.
4. Посмотреть логи:

```text
/Users/natalie/.local/share/tg-dashboard-agent-runtime/data/tg_agent_logs/
```

5. Если видно новый `business_connection_id`, добавить его в `TG_AGENT_ALLOWED_BUSINESS_CONNECTION_IDS`.

## 10. Дашборд

Бот читает данные не напрямую из браузера, а из локального слоя данных дашборда.

Основная переменная:

```bash
TG_DASHBOARD_DATA_DIR=...
```

Проверка:

```bash
set -a; source "$HOME/.local/share/tg-dashboard-agent-runtime/deployment-access/tg-dashboard-agent.env"; set +a
python3 "$HOME/.local/share/tg-dashboard-agent-runtime/scripts/tg_dashboard_agent.py" \
  --config "$HOME/.local/share/tg-dashboard-agent-runtime/data/tg_dashboard_agent.config.example.json" \
  preview "какая выручка за вчера"
```

Правильный ответ содержит:

- период;
- сумму;
- источник;
- ссылку на страницу дашборда;
- последнюю дату источника или предупреждение об отставании.

## 11. ClickUp

Бот подключён к ClickUp и работает с главным списком `ОГ`.

Важные переменные:

```bash
CLICKUP_API_TOKEN=...
CLICKUP_TEAM_ID=...
CLICKUP_LIST_ID=...
CLICKUP_LIST_NAME=ОГ
CLICKUP_TELEGRAM_USER_MAP=telegram_user_id:clickup_user_id
```

Проверка:

```text
задачи по мне
какие задачи у Фатхулло
задачи по ОГ
```

## 12. Голос

Распознавание голосовых работает через локальный транскрибатор.

Голосовой ответ высокого качества через OpenAI TTS готов в коде, но нужен API key клиента:

```bash
OPENAI_API_KEY=...
TG_AGENT_TTS_PROVIDER=openai
TG_AGENT_TTS_MODEL=gpt-4o-mini-tts
TG_AGENT_TTS_VOICE=nova
```

Расходы должны идти с аккаунта клиента.

## 13. Что сейчас известно как открытые gates

Смотреть:

```text
docs/GATES.md
```

Главное:

- до переноса на сервер бот зависит от локального компьютера;
- Google Sheets sync журнала не настроен, журнал живёт локально;
- для качественного голосового ответа нужен `OPENAI_API_KEY` клиента.

## 14. Команда для Claude Фатхулло

Можно дать Claude такой стартовый промпт:

```text
Ты продолжаешь работу над Telegram-ботом @TGkdagent_bot.

Сначала прочитай:
- README.md
- docs/HANDOFF_CLAUDE.md
- docs/OPERATIONS.md
- docs/SETUP.md
- docs/GATES.md
- docs/COMMANDS.md

Рабочий порядок:
- не печатай секреты из .env;
- не коммить .env и историю чатов;
- изменения сначала вноси в starter kit;
- после проверки переноси их в live-файл;
- перед перезапуском проверь, что не запускаешь второй polling-процесс;
- после правок запускай self-test, doctor и live-status;
- после успешной проверки коммить и пушь starter kit.

Цель ближайшей поддержки:
- сохранить стабильную работу регулярных сводок;
- улучшать качество сводок по группам и личным чатам;
- поддерживать русский/узбекский язык ответа;
- подключать новые источники через env и отдельные функции без раскрытия секретов.
```
