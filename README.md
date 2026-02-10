# Telegram AI Channel Parser Bot

Telegram-бот, который отслеживает каналы и веб-источники, анализирует новости через DeepSeek LLM и присылает алерты о важных событиях.

## Возможности

- **Авторизация** — логин/пароль, один аккаунт можно привязать к нескольким Telegram-аккаунтам
- **Управление подписками** — добавление/удаление Telegram-каналов и веб-ссылок
- **Парсинг каналов** — автоматический сбор постов из Telegram-каналов через Telethon
- **Парсинг веб-сайтов** — RSS + HTML scraping fallback
- **AI-анализ** — суммаризация новостей через DeepSeek LLM
- **Алерты:**
  - Похожая новость в нескольких каналах (vector search + LLM подтверждение)
  - Аномально высокое количество реакций на посте
- **Дайджест** — ежедневная подборка топ-новостей (настраиваемое время + по кнопке)

## Стек

- Python 3.11+, aiogram 3, Telethon
- PostgreSQL 16 + pgvector (Docker)
- DeepSeek API (LLM), sentence-transformers (embeddings)
- APScheduler, SQLAlchemy 2.0, Alembic

## Быстрый старт

### 1. Клонируйте репозиторий

```bash
git clone <url>
cd telegram-ai-channel-parser
```

### 2. Настройте окружение

```bash
cp .env.example .env
# Заполните .env своими значениями:
# - BOT_TOKEN (от @BotFather)
# - TELEGRAM_API_ID, TELEGRAM_API_HASH (от https://my.telegram.org)
# - TELEGRAM_PHONE (ваш номер для Telethon)
# - DEEPSEEK_API_KEY (от DeepSeek)
```

### 3. Запустите PostgreSQL

```bash
docker-compose up -d
```

### 4. Установите зависимости

```bash
python -m venv venv
venv\Scripts\activate     # Windows
# source venv/bin/activate  # Linux/Mac

pip install -r requirements.txt
```

### 5. Примените миграции

```bash
alembic upgrade head
```

### 6. Заполните базовый пул каналов

```bash
python -m app.seed
```

### 7. Запустите бота

```bash
python -m app.main
```

> При первом запуске Telethon попросит ввести код авторизации в консоли.

## Структура проекта

```
app/
├── main.py                 # Точка входа
├── config.py               # Настройки (.env)
├── seed.py                 # Заполнение базового пула
├── db/
│   ├── models.py           # SQLAlchemy модели
│   ├── database.py         # Engine, session
│   └── repositories.py     # CRUD-операции
├── bot/
│   ├── bot.py              # aiogram Bot + Dispatcher
│   ├── middlewares.py       # Auth middleware
│   ├── states.py           # FSM-состояния
│   ├── keyboards.py        # Inline-клавиатуры
│   └── handlers/
│       ├── auth.py         # Логин / регистрация
│       ├── menu.py         # Главное меню
│       ├── channels.py     # Управление каналами
│       ├── links.py        # Управление ссылками
│       ├── digest.py       # Дайджест
│       └── settings.py     # Настройки
├── services/
│   ├── telegram_parser.py  # Telethon парсинг каналов
│   ├── web_parser.py       # RSS + HTML парсинг
│   ├── llm_client.py       # DeepSeek API
│   ├── embedding.py        # sentence-transformers
│   ├── similarity.py       # Поиск похожих постов
│   ├── alerts.py           # Логика алертов
│   └── digest.py           # Генерация дайджестов
└── scheduler/
    └── tasks.py            # APScheduler задачи
```

## Переменные окружения

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | Токен Telegram-бота от @BotFather |
| `TELEGRAM_API_ID` | API ID от my.telegram.org |
| `TELEGRAM_API_HASH` | API Hash от my.telegram.org |
| `TELEGRAM_PHONE` | Номер телефона для Telethon |
| `DEEPSEEK_API_KEY` | API-ключ DeepSeek |
| `DEEPSEEK_BASE_URL` | URL API DeepSeek (по умолчанию https://api.deepseek.com) |
| `DEEPSEEK_MODEL` | Модель DeepSeek (по умолчанию deepseek-chat) |
| `POSTGRES_*` | Настройки PostgreSQL |
| `TELEGRAM_PARSE_INTERVAL` | Интервал парсинга каналов в минутах (по умолчанию 10) |
| `WEB_PARSE_INTERVAL` | Интервал парсинга веб-ссылок в минутах (по умолчанию 30) |
| `SIMILARITY_THRESHOLD` | Порог похожести для алертов (по умолчанию 0.82) |
| `REACTIONS_MULTIPLIER` | Множитель для алерта реакций (по умолчанию 3.0) |
