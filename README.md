# Telegram AI Channel Parser Bot

Telegram-бот, который отслеживает каналы и веб-источники, анализирует новости через DeepSeek LLM и присылает алерты о важных событиях.

## Возможности

- **Авторизация** — логин/пароль, один аккаунт можно привязать к нескольким Telegram-аккаунтам
- **Управление подписками** — добавление/удаление Telegram-каналов и веб-ссылок
- **Парсинг каналов** — автоматический сбор постов из Telegram-каналов через Telethon
- **Парсинг веб-сайтов и API-источников** — RSS/HTML + Reddit/GitHub/Product Hunt
- **AI-анализ** — один вызов DeepSeek на пост: summary + relevance + CoreAI score
- **Теги новостей** — DeepSeek присваивает 1-3 хештега из фиксированной таксономии (для поиска в чате)
- **Фильтр релевантности** — pre-filter + LLM, чтобы сократить лишние запросы
- **Алерты:**
  - Похожая новость в нескольких каналах (кластерный alert вместо спама по копипастам)
  - Trend-update: если тот же кластер продолжает расти по источникам, бот шлёт обновление популярности
  - Аномально высокое количество реакций на посте
  - Кластеризация в БД: canonical-текст/summary, источники, mention_count, embedding
  - Контекст из других источников (Tavily) в каждом алерте
- **Дайджест** — ежедневная подборка топ-новостей (настраиваемое время + по кнопке)
- **Поиск источников** — Search -> Pre-filter -> Dedup для Telegram/Web + Reddit/GitHub/Product Hunt (через API-ключи)

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

**Локально (Windows/Linux/Mac, с GPU или без):**

```bash
python -m venv venv
venv\Scripts\activate     # Windows
# source venv/bin/activate  # Linux/Mac

pip install -r requirements.txt
```

**Сервер без GPU (VPS, мало места на диске):**  
По умолчанию pip ставит PyTorch с поддержкой CUDA (~2.5 ГБ). Чтобы не забить диск, сначала установите CPU-версию PyTorch, затем остальное:

```bash
python -m venv venv
source venv/bin/activate   # Linux

# Сначала PyTorch только для CPU (~200 МБ вместо ~2.5 ГБ)
pip install torch --index-url https://download.pytorch.org/whl/cpu

# Потом все остальные зависимости
pip install -r requirements.txt
```

Если при первой установке уже появилась ошибка `No space left on device`, освободите место (например, `pip cache purge`, удалите частично установленные пакеты или увеличьте диск), затем выполните эти две команды по порядку.

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
2. **Нормализация и дедуп** — считается hash, дубликаты переиспользуют существующий анализ
3. **Единый LLM-анализ** — DeepSeek возвращает summary + AI relevance + CoreAI score
4. **Embedding** — локальная модель превращает summary в вектор (384-dim)
5. **Кластеризация** — пост привязывается к canonical news cluster (pgvector + точечный LLM-check)
6. **Алерт по кластеру** — один сводный alert на событие с несколькими источниками
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
| `REDDIT_CLIENT_ID` | Client ID Reddit API для discovery и планового скана |
| `REDDIT_CLIENT_SECRET` | Client Secret Reddit API |
| `REDDIT_USER_AGENT` | User-Agent для Reddit API |
| `GITHUB_API_KEY` | GitHub API token для поиска репозиториев |
| `PRODUCTHUNT_API_KEY` | Product Hunt API token (GraphQL) |
| `POSTGRES_USER` | Имя пользователя PostgreSQL (по умолчанию `tg_parser`) |
| `POSTGRES_PASSWORD` | Пароль PostgreSQL (по умолчанию `tg_parser_secret`) |
| `POSTGRES_DB` | Имя базы данных (по умолчанию `tg_parser_db`) |
| `POSTGRES_HOST` | Хост PostgreSQL (по умолчанию `localhost`) |
| `POSTGRES_PORT` | Порт PostgreSQL (по умолчанию `5432`) |
| `TELEGRAM_PARSE_INTERVAL` | Интервал парсинга каналов в минутах (по умолчанию `10`) |
| `WEB_PARSE_INTERVAL` | Интервал парсинга веб-ссылок в минутах (по умолчанию `30`) |
| `API_SOURCE_MAX_ITEMS` | Сколько элементов брать за один скан API-источника (по умолчанию `20`) |
| `API_SOURCE_LOOKBACK_HOURS` | Окно свежести API-новостей (по умолчанию `48` часов) |
| `SIMILARITY_THRESHOLD` | Порог похожести для алертов (по умолчанию `0.82`) |
| `REACTIONS_MULTIPLIER` | Множитель для алерта реакций (по умолчанию `3.0`) |
| `CLUSTER_MIN_MENTIONS` | Минимум упоминаний в кластере для similarity alert (по умолчанию `2`) |
| `COREAI_ALERT_THRESHOLD` | Порог важности CoreAI для выделения кластера (по умолчанию `0.6`) |
