# personal_ai_engeneer

Автономный coding-агент: тянет таски из Notion → пишет код через Claude Code → создаёт MR в GitLab → мержит после твоего апрува в Telegram.

> **Фаза 0** (сейчас): обнаружение тасок в Notion + Telegram-уведомления с кнопками опт-ина. Без Claude Code, без GitLab.

## Стек

- Python 3.12, `uv`
- `aiogram` v3 — Telegram
- `notion-client` — Notion API
- `aiosqlite` — state
- Docker Compose — runtime

## Архитектура (Фаза 0)

```
┌─────────────┐     ┌──────────────┐     ┌────────────┐
│   Notion    │────▶│  Poller      │────▶│  Telegram  │
│  Tasks DB   │     │  (every 5m)  │     │ (опт-ин)   │
└─────────────┘     └──────┬───────┘     └────────────┘
                           │
                           ▼
                      SQLite (seen + decisions)
```

## Setup

### 1. Создать Notion integration

1. https://www.notion.so/profile/integrations → **+ New integration**
2. Тип: **Internal**, capabilities: Read content, Read user info (with email opt.)
3. Скопировать **Internal Integration Secret** → в `.env` как `NOTION_TOKEN`
4. Открыть БД [🔎 Tasks](https://www.notion.so/freedom-ai-labs/dda50eb624ed41a2a700bd554b76f770) → ••• → **Connections** → добавить созданную интеграцию

### 2. Создать Telegram bot

1. https://t.me/BotFather → `/newbot` → имя, username
2. Токен → в `.env` как `TELEGRAM_BOT_TOKEN`
3. Свой user ID узнать у https://t.me/userinfobot → в `.env` как `TELEGRAM_ALLOWED_USER_ID`

### 3. Заполнить `.env`

```bash
cp .env.example .env
# отредактировать, заполнить пустые поля
```

### 4. Запуск

```bash
docker compose up --build
```

Логи:
```bash
docker compose logs -f
```

Стоп:
```bash
docker compose down
```

### Разработка локально (без Docker)

```bash
uv sync
cp .env.example .env  # заполнить
uv run python -m ai_agent.main
```

## Команды бота

- `/ping` — проверка живости
- `/start` — приветствие, список команд
- `/list` — таски ожидающие решения
- `/status` — счётчики (pending / accepted / skipped)

При появлении новой таски в Notion бот пришлёт карточку с кнопками:
- **▶️ Взять** — пометить как принятую в работу (в Фазе 1 здесь начнётся кодинг)
- **⏭ Пропустить** — игнорировать
- **⏰ Позже** — отложить (TODO: переспросить через N часов)

## Что дальше (roadmap)

- **Фаза 1:** Claude Code integration, git worktrees, GitLab MR
- **Фаза 2:** review-агент, pre-commit gates, CI waiting
- **Фаза 3:** условный auto-merge
