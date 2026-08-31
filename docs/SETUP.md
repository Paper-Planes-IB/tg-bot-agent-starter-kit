# Настройка нового бота

## 1. Telegram

Создать бота в BotFather, получить токен и записать его в `.env`:

```bash
TG_DASHBOARD_BOT_TOKEN=...
```

После запуска написать боту `/whoami` от имени каждого пользователя. Их `user_id` добавить в:

```bash
TG_AGENT_ALLOWED_USER_IDS=111,222,333
```

Если бот должен работать в группах, добавить его в группу, отправить `/whoami` или любое сообщение, узнать `chat_id` группы и добавить:

```bash
TG_AGENT_ALLOWED_CHAT_IDS=-100111,-100222
```

Для Telegram Business / автоматизации личных чатов:

```bash
TG_AGENT_ALLOWED_BUSINESS_CONNECTION_IDS=...
```

`business_connection_id` берётся из логов после подключения бота в настройках Telegram Business. Пока это поле не заполнено, бот может видеть событие, но не будет сохранять сообщения из личных чатов.

Реакции Telegram лучше держать выключенными, если нет отдельной причины их включать:

```bash
TG_AGENT_REACTIONS_ENABLED=0
```

Приоритеты для сводок задаются списками через запятую:

```bash
TG_AGENT_PRIORITY_BUSINESS_CHATS=Мухрим Абдулазизов,Муборак,Мохинул,Абдуазиз
TG_AGENT_PRIORITY_GROUP_CHATS=TG-PP,TG+PP,Опер группа,ОГ
```

Сообщения из этих личных чатов и групп будут попадать в сводки выше остальных.

Если выбранные для сводки сообщения в основном на узбекском, бот пишет содержательную сводку и заголовки на узбекском.

Ролевая логика для Toshkent Gullari уже встроена в промпт сводок:

- Мухрим Абдулазизов — CEO, его не ставить операционным исполнителем без прямого поручения;
- Мубарак, Абдуазиз и Махинур — соучредители;
- Абдулазиз, Мухаммаджон и Фатхулло — участники опергруппы;
- остальные сотрудники определяются по контексту.

Регулярные сводки считаются по слотам: каждая берёт сообщения после предыдущего выпуска такого же типа и до текущего scheduled-времени. В тексте сводки всегда показывается строка `Период`.

## 2. Группа с записями звонков

Бота можно добавить в отдельную группу, куда команда скидывает записи звонков.

Что сделать:

1. Добавить бота в группу с записями.
2. Отправить в этой группе `/whoami`.
3. Добавить `chat_id` группы в доступы:

```bash
TG_AGENT_ALLOWED_CHAT_IDS=-100111,-100222
```

4. Добавить этот же `chat_id` в список групп, где любые аудио/видео считаются записями звонков:

```bash
TG_AGENT_CALL_RECORDING_CHAT_IDS=-100111
```

5. Если записи приходят через Telegram Business / личные чаты, добавить личные `chat_id` в отдельное поле:

```bash
TG_AGENT_CALL_RECORDING_BUSINESS_CHAT_IDS=123456789,987654321
```

6. Указать папку хранения. Можно использовать папку Google Drive Desktop:

```bash
TG_AGENT_CALL_RECORDINGS_DIR=/absolute/path/to/Google Drive/Call recordings
TG_AGENT_CALL_RECORDING_MAX_MB=200
```

Что будет делать бот:

- сохранять голосовые, аудио, видео и аудио/видео-документы из этой группы;
- складывать файлы по датам;
- писать строку в журнал `data/tg_call_recordings.jsonl`;
- короткие записи расшифровывать и разбирать на итог, решения, задачи, риски и вопросы;
- по команде `/call_recordings` показывать последние сохранённые записи.

Если `TG_AGENT_CALL_RECORDING_CHAT_IDS` пустой, бот сохраняет медиа из групп только когда в подписи есть слова `звон`, `созвон`, `call`, `recording` или `запис`.
Если `TG_AGENT_CALL_RECORDING_BUSINESS_CHAT_IDS` пустой, бот не считает каждое голосовое из лички записью звонка, но `/call_recordings` покажет свежие голосовые/файлы из бизнес-личек как кандидатов.

## 3. Дашборд

Если бот отвечает на вопросы по дашборду, положить JSON-данные в `data/dashboard` или указать путь:

```bash
TG_DASHBOARD_DATA_DIR=/absolute/path/to/dashboard/data
TG_DASHBOARD_BASE_URL=https://example.com/dashboard/
```

## 4. ClickUp

Заполнить:

```bash
CLICKUP_API_TOKEN=...
CLICKUP_TEAM_ID=...
CLICKUP_LIST_ID=...
CLICKUP_LIST_NAME=...
CLICKUP_TELEGRAM_USER_MAP=telegram_user_id:clickup_user_id
```

Для нескольких пользователей:

```bash
CLICKUP_TELEGRAM_USER_MAP=111:cu_1,222:cu_2
```

## 5. Brain / Codex

Если нужен свободный диалог и интерпретация, включить:

```bash
TG_AGENT_BRAIN_ENABLED=1
TG_AGENT_BRAIN_COMMAND=codex exec --skip-git-repo-check --output-last-message {output} -
```

Без brain бот всё равно выполняет шаблонные команды, журнал, дашборд, ClickUp и регулярные сообщения.

## 6. Проверка

```bash
set -a; source .env; set +a
python3 scripts/tg_bot_agent.py --config data/tg_bot_agent.config.example.json self-test
python3 scripts/tg_bot_agent.py --config data/tg_bot_agent.config.example.json telegram-check
python3 scripts/tg_bot_agent.py --config data/tg_bot_agent.config.example.json doctor
```
