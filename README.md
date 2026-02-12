# Telegram AI Channel Parser Bot

Telegram-бот, который отслеживает каналы и веб-источники, анализирует новости через DeepSeek LLM и присылает алерты о важных событиях.

## Возможности

- **Авторизация** — логин/пароль, один аккаунт можно привязать к нескольким Telegram-аккаунтам
- **Управление подписками** — добавление/удаление Telegram-каналов и веб-ссылок
- **Парсинг каналов** — автоматический сбор постов из Telegram-каналов через Telethon
- **Парсинг веб-сайтов** — RSS + HTML scraping fallback
- **AI-анализ** — суммаризация новостей через DeepSeek LLM
- **Фильтр релевантности** — автоматическое отсеивание рекламы, промо и офтопа (LLM + ключевые слова)
- **Алерты:**
  - Похожая новость в нескольких каналах (vector search + LLM подтверждение)
  - Аномально высокое количество реакций на посте
  - Кластеризация: если одна новость в 3+ каналах — один сводный алерт
  - Контекст из других источников (Tavily) в каждом алерте
- **Дайджест** — ежедневная подборка топ-новостей (настраиваемое время + по кнопке)
- **Поиск источников** — кнопка "Найти ещё источники" под дайджестом и алертами, поиск по Telegram-каналам и веб-сайтам

## Стек

- Python 3.11+, aiogram 3, Telethon
- PostgreSQL 16 + pgvector (Docker)
- DeepSeek API (LLM), sentence-transformers (embeddings)
- Tavily API (поиск источников и контекста)
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
```

Заполните `.env` своими значениями:
- `BOT_TOKEN` — токен бота от @BotFather
- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` — от https://my.telegram.org
- `TELEGRAM_PHONE` — ваш номер телефона для Telethon
- `DEEPSEEK_API_KEY` — API-ключ от DeepSeek
- `TAVILY_API_KEY` — API-ключ от https://tavily.com (для поиска источников и контекста)

### 3. Запустите PostgreSQL

```bash
docker compose up -d
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

> Скрипт добавляет дефолтные каналы и веб-источники. Повторный запуск безопасен — дубликаты не создаются.
> Если хотите изменить список, отредактируйте `app/seed.py` и запустите снова.

### 7. Запустите бота

```bash
python -m app.main
```

> При первом запуске Telethon попросит ввести код авторизации в консоли.

### Повторный запуск (после перезагрузки)

```bash
cd telegram-ai-channel-parser
venv\Scripts\activate     # Windows
docker compose up -d      # если PostgreSQL остановлен
python -m app.main
```

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
│       ├── menu.py         # Главное меню, /status, /help
│       ├── channels.py     # Управление каналами
│       ├── links.py        # Управление ссылками
│       ├── digest.py       # Дайджест + поиск источников
│       └── settings.py     # Настройки (время дайджеста, таймзона)
├── services/
│   ├── telegram_parser.py  # Telethon парсинг каналов
│   ├── web_parser.py       # RSS + HTML парсинг
│   ├── llm_client.py       # DeepSeek API (саммари, сравнение, фильтр ИИ)
│   ├── embedding.py        # sentence-transformers (all-MiniLM-L6-v2)
│   ├── similarity.py       # Поиск похожих постов (pgvector + LLM)
│   ├── alerts.py           # Обработка постов, кластеризация, алерты
│   ├── digest.py           # Генерация дайджестов
│   └── web_search.py       # Поиск источников (Tavily + Telethon)
└── scheduler/
    └── tasks.py            # APScheduler задачи
```

## Как работает анализ

1. **Парсинг** — каждые 10 мин (Telegram) и 30 мин (веб) собираются новые посты
2. **Саммари** — DeepSeek генерирует краткую выжимку каждого поста
3. **Фильтр** — LLM проверяет, что пост про ИИ/ML (реклама и офтоп отсеиваются)
4. **Embedding** — локальная модель превращает текст в вектор (384-dim)
5. **Поиск похожих** — pgvector ищет ближайшие векторы в БД, DeepSeek подтверждает
6. **Кластеризация** — если одна новость в 3+ каналах, отправляется один сводный алерт
7. **Реакции** — если реакций в 3x+ раз больше среднего, отправляется алерт
8. **Дайджест** — в заданное время (или по кнопке) собирает топ-новости за 24ч

## Переменные окружения

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | Токен Telegram-бота от @BotFather |
| `TELEGRAM_API_ID` | API ID от my.telegram.org |
| `TELEGRAM_API_HASH` | API Hash от my.telegram.org |
| `TELEGRAM_PHONE` | Номер телефона для Telethon |
| `DEEPSEEK_API_KEY` | API-ключ DeepSeek |
| `DEEPSEEK_BASE_URL` | URL API DeepSeek (по умолчанию `https://api.deepseek.com`) |
| `DEEPSEEK_MODEL` | Модель DeepSeek (по умолчанию `deepseek-chat`) |
| `TAVILY_API_KEY` | API-ключ Tavily для поиска источников (от https://tavily.com) |
| `POSTGRES_USER` | Имя пользователя PostgreSQL (по умолчанию `tg_parser`) |
| `POSTGRES_PASSWORD` | Пароль PostgreSQL (по умолчанию `tg_parser_secret`) |
| `POSTGRES_DB` | Имя базы данных (по умолчанию `tg_parser_db`) |
| `POSTGRES_HOST` | Хост PostgreSQL (по умолчанию `localhost`) |
| `POSTGRES_PORT` | Порт PostgreSQL (по умолчанию `5432`) |
| `TELEGRAM_PARSE_INTERVAL` | Интервал парсинга каналов в минутах (по умолчанию `10`) |
| `WEB_PARSE_INTERVAL` | Интервал парсинга веб-ссылок в минутах (по умолчанию `30`) |
| `SIMILARITY_THRESHOLD` | Порог похожести для алертов (по умолчанию `0.82`) |
| `REACTIONS_MULTIPLIER` | Множитель для алерта реакций (по умолчанию `3.0`) |
