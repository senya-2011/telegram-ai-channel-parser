# Архитектура проекта: Telegram AI Channel Parser

## Общее описание

Бот автоматически собирает новости из Telegram-каналов и веб-сайтов, анализирует их с помощью ИИ, фильтрует рекламу/офтоп, находит похожие новости из разных источников и отправляет пользователю алерты и ежедневные дайджесты.

---

## Общая архитектура системы

```mermaid
graph TB
    subgraph external [Внешние сервисы]
        TG_CHANNELS[Telegram каналы]
        WEB_SITES[Веб-сайты / RSS]
        DEEPSEEK[DeepSeek API]
        TAVILY[Tavily API]
    end

    subgraph bot_app [Приложение]
        MAIN[main.py — точка входа]
        SCHEDULER[APScheduler — планировщик задач]
        BOT[aiogram Bot — интерфейс пользователя]

        subgraph parsing [Парсинг]
            TG_PARSER[telegram_parser.py — Telethon]
            WEB_PARSER[web_parser.py — httpx / feedparser / BS4]
        end

        subgraph analysis [Анализ]
            LLM[llm_client.py — DeepSeek]
            EMB[embedding.py — sentence-transformers]
            SIM[similarity.py — pgvector + LLM]
            ALERTS[alerts.py — пайплайн обработки]
        end

        subgraph user_facing [Пользовательский интерфейс]
            HANDLERS[handlers/ — меню, каналы, ссылки, настройки]
            DIGEST_H[handlers/digest.py — дайджест + поиск источников]
            DIGEST_S[digest.py — генерация дайджеста]
            SEARCH[web_search.py — поиск источников]
        end
    end

    subgraph storage [Хранилище]
        PG[(PostgreSQL + pgvector)]
    end

    TG_CHANNELS -->|Telethon userbot| TG_PARSER
    WEB_SITES -->|HTTP / RSS| WEB_PARSER
    TG_PARSER --> PG
    WEB_PARSER --> PG
    PG --> ALERTS
    ALERTS -->|суммаризация, фильтр| LLM
    ALERTS -->|embedding| EMB
    ALERTS -->|поиск похожих| SIM
    SIM -->|vector search| PG
    SIM -->|подтверждение| LLM
    ALERTS -->|алерты| BOT
    DIGEST_S -->|посты за 24ч| PG
    DIGEST_S -->|генерация текста| LLM
    SEARCH -->|каналы| TG_CHANNELS
    SEARCH -->|веб| TAVILY
    SCHEDULER --> TG_PARSER
    SCHEDULER --> WEB_PARSER
    SCHEDULER --> ALERTS
    SCHEDULER --> DIGEST_S
    BOT --> HANDLERS
    BOT --> DIGEST_H
    MAIN --> BOT
    MAIN --> SCHEDULER
```

---

## Пайплайн обработки постов

Каждые 10 минут (Telegram) и 30 минут (веб) запускается парсинг. Новые посты проходят через следующий пайплайн:

```mermaid
flowchart TD
    A[Новый пост из канала/сайта] --> B[DeepSeek: саммари 2-3 предложения]
    B --> C{DeepSeek: это новость про ИИ?}
    C -->|NO: реклама / промо / офтоп| D[Сохранить в БД, пропустить алерты]
    C -->|YES: реальная ИИ-новость| E[sentence-transformers: embedding 384-dim]
    E --> F[Сохранить summary + embedding в БД]
    F --> G[pgvector: найти похожие посты за 48ч]
    G --> H{Есть кандидаты с cosine >= 0.82?}
    H -->|Нет| I[Проверить аномалию реакций]
    H -->|Да| J[DeepSeek: подтвердить что это ОДНА новость]
    J --> K{LLM подтвердил?}
    K -->|Нет| I
    K -->|Да| L[Кластеризация: объединить все похожие в один алерт]
    L --> M[Tavily: найти контекст из других источников]
    M --> N[Отправить сводный алерт пользователю]
    N --> I
    I --> O{Реакций в 3x+ раз больше среднего?}
    O -->|Нет| P[Готово]
    O -->|Да| Q[Отправить алерт о популярном посте]
    Q --> P
```

---

## Система сравнения постов (двухэтапная)

Сравнение работает в два этапа для оптимального баланса скорости и точности:

```mermaid
flowchart LR
    subgraph stage1 [Этап 1: Быстрый — pgvector]
        POST[Новый пост с embedding] --> PGVEC[PostgreSQL cosine_distance]
        PGVEC --> TOP10[Топ-10 ближайших за 48ч]
        TOP10 --> FILTER[Фильтр: cosine >= 0.82 + разные каналы]
        FILTER --> CANDIDATES["1-5 кандидатов"]
    end

    subgraph stage2 [Этап 2: Точный — DeepSeek LLM]
        CANDIDATES --> LLM_CHECK["DeepSeek: Это одна новость?"]
        LLM_CHECK --> YES_NO{Да / Нет}
        YES_NO -->|Да| CONFIRMED[Подтверждено: похожие]
        YES_NO -->|Нет| REJECTED[Отклонено]
    end
```

**Зачем два этапа?**
- **pgvector** (Этап 1) — математическое сравнение векторов, работает за миллисекунды. Отсеивает 99% постов. Но может давать ложные срабатывания (два текста "про OpenAI" не обязательно про одно событие).
- **DeepSeek** (Этап 2) — понимает смысл текста и может отличить "OpenAI запускает рекламу" от "OpenAI выпустила новую модель". Но дорогой и медленный (1-2 сек на вызов), поэтому используется только для 1-5 кандидатов.

---

## Кластеризация алертов

Если одна новость найдена в 3+ каналах за один скан, отправляется один сводный алерт:

```mermaid
flowchart TD
    subgraph batch [Батч новых постов]
        P1["Пост A — канал 1"]
        P2["Пост B — канал 2"]
        P3["Пост C — канал 3"]
        P4["Пост D — канал 4 (другая тема)"]
    end

    P1 --> CLUSTER1["Кластер: A + B + C — одна новость"]
    P2 --> CLUSTER1
    P3 --> CLUSTER1
    P4 --> CLUSTER2["Пост D — отдельный"]

    CLUSTER1 --> ALERT1["1 алерт: Новость в 3 каналах + ссылки на все 3"]
    CLUSTER2 --> CHECK["Нет похожих -> нет алерта"]
```

---

## Схема базы данных

```mermaid
erDiagram
    User ||--o{ UserTelegramLink : "привязка аккаунтов"
    User ||--o| UserSettings : "настройки"
    User ||--o{ UserSource : "подписки"
    User ||--o{ Alert : "алерты"
    Source ||--o{ UserSource : "подписчики"
    Source ||--o{ Post : "посты"
    Post ||--o{ Alert : "алерты"

    User {
        int id PK
        string username UK
        string password_hash
        datetime created_at
    }

    UserTelegramLink {
        int id PK
        int user_id FK
        bigint telegram_user_id UK
    }

    UserSettings {
        int id PK
        int user_id FK
        string digest_time "HH:MM"
        string timezone "Europe/Moscow"
    }

    Source {
        int id PK
        string type "telegram / web"
        string identifier "@channel / URL"
        string title
        bool is_default
    }

    UserSource {
        int id PK
        int user_id FK
        int source_id FK
    }

    Post {
        int id PK
        int source_id FK
        string external_id "msg_id / URL"
        text content
        text summary "DeepSeek"
        vector embedding "384-dim"
        int reactions_count
        float reactions_ratio
        datetime published_at
        datetime parsed_at
    }

    Alert {
        int id PK
        int user_id FK
        int post_id FK
        string alert_type "similar / reactions"
        text reason
        bool is_sent
        datetime created_at
    }
```

---

## Стек технологий

```mermaid
graph LR
    subgraph frontend [Пользовательский интерфейс]
        AIOGRAM[aiogram 3 — Telegram Bot API]
    end

    subgraph backend [Бэкенд]
        PYTHON[Python 3.11+]
        TELETHON[Telethon — Telegram Client API]
        HTTPX[httpx — HTTP-клиент]
        FEEDPARSER[feedparser — RSS]
        BS4[BeautifulSoup4 — HTML scraping]
        APSCHED[APScheduler — планировщик]
        BCRYPT[bcrypt — хеширование паролей]
        PYDANTIC[pydantic-settings — конфигурация]
    end

    subgraph ai [AI / ML]
        DEEPSEEK_AI["DeepSeek API — LLM"]
        SENTENCE["sentence-transformers — embedding"]
        TAVILY_AI["Tavily API — веб-поиск"]
    end

    subgraph db [База данных]
        POSTGRES["PostgreSQL 16"]
        PGVEC_EXT["pgvector — векторный поиск"]
        SQLA["SQLAlchemy 2.0 + asyncpg"]
        ALEMBIC_T["Alembic — миграции"]
        DOCKER["Docker Compose"]
    end

    AIOGRAM --> PYTHON
    TELETHON --> PYTHON
    HTTPX --> PYTHON
    DEEPSEEK_AI --> PYTHON
    SENTENCE --> PYTHON
    TAVILY_AI --> PYTHON
    SQLA --> POSTGRES
    PGVEC_EXT --> POSTGRES
    ALEMBIC_T --> SQLA
    DOCKER --> POSTGRES
```

### Что делает каждая технология

| Технология | Роль | Почему выбрана |
|---|---|---|
| **aiogram 3** | Telegram Bot API — интерфейс пользователя | Асинхронный, современный, FSM из коробки |
| **Telethon** | Telegram Client API — парсинг каналов | Читает посты, реакции, ищет каналы (userbot) |
| **PostgreSQL + pgvector** | Хранение данных + векторный поиск | Одна БД для всего: данные + cosine similarity |
| **SQLAlchemy 2.0 + asyncpg** | ORM + async драйвер | Типизированные модели, async/await |
| **Alembic** | Миграции БД | Версионирование схемы базы |
| **DeepSeek API** | LLM для анализа текста | Суммаризация, сравнение, фильтрация, дайджест |
| **sentence-transformers** | Локальные embeddings (all-MiniLM-L6-v2) | Быстро, бесплатно, 384-dim вектор |
| **Tavily API** | Веб-поиск | Поиск контекста для алертов, новых источников |
| **APScheduler** | Планировщик задач | Периодический парсинг, отправка дайджестов |
| **httpx + feedparser + BS4** | Парсинг веб-сайтов | RSS-ленты + fallback на HTML scraping |
| **bcrypt** | Хеширование паролей | Безопасное хранение паролей пользователей |
| **Docker Compose** | Контейнеризация PostgreSQL | Простой запуск БД одной командой |

---

## Жизненный цикл пользователя

```mermaid
sequenceDiagram
    actor User as Пользователь
    participant Bot as Telegram Bot
    participant DB as PostgreSQL
    participant Sched as Scheduler
    participant Parser as Парсеры
    participant AI as DeepSeek + Embeddings

    User->>Bot: /start
    Bot->>User: Регистрация или вход
    User->>Bot: Логин + пароль
    Bot->>DB: Создать/найти пользователя
    Bot->>DB: Подписать на дефолтные каналы
    Bot->>User: Главное меню

    User->>Bot: Добавить канал @example
    Bot->>DB: Создать источник + подписку

    Note over Sched: Каждые 10 мин
    Sched->>Parser: Парсить каналы
    Parser->>DB: Сохранить новые посты

    Sched->>AI: Обработать посты
    AI->>AI: Саммари -> Фильтр ИИ -> Embedding
    AI->>DB: Сохранить анализ
    AI->>DB: Поиск похожих (pgvector)
    AI->>AI: LLM подтверждение
    AI->>Bot: Отправить алерт

    Bot->>User: Алерт: похожая новость в 3 каналах!

    User->>Bot: Кнопка "Найти ещё про это"
    Bot->>AI: Tavily + Telethon поиск
    Bot->>User: Список новых источников
    User->>Bot: Подписаться на источник

    Note over Sched: В 20:00 (время пользователя)
    Sched->>DB: Посты за 24ч
    Sched->>AI: Сгенерировать дайджест
    Sched->>Bot: Отправить дайджест
    Bot->>User: Дайджест за сегодня + кнопки
```

---

## Фильтрация контента

Система фильтрации работает на двух уровнях:

```mermaid
flowchart TD
    subgraph level1 [Уровень 1: LLM-фильтр — при обработке постов]
        NEW_POST[Новый пост] --> SUMMARY[DeepSeek: саммари]
        SUMMARY --> LLM_FILTER["DeepSeek: YES/NO — это ИИ-новость?"]
        LLM_FILTER -->|YES| PASS1[Проходит в алерты]
        LLM_FILTER -->|NO: реклама, промо, курсы, вакансии| SKIP1[Пропускается]
    end

    subgraph level2 [Уровень 2: Keyword-фильтр — при генерации дайджеста]
        DIGEST_POSTS[Посты для дайджеста] --> KW_FILTER["Быстрая проверка: ключевые слова ИИ"]
        KW_FILTER -->|Содержит: ai, gpt, нейросет...| PASS2[Включается в дайджест]
        KW_FILTER -->|Не содержит| SKIP2[Не включается]
    end

    subgraph level3 [Уровень 3: Keyword-фильтр — для Tavily результатов]
        TAVILY_RES[Результаты Tavily] --> KW_FILTER2["Проверка title + content на ИИ-слова"]
        KW_FILTER2 -->|Релевантно| PASS3[Показать пользователю]
        KW_FILTER2 -->|Не релевантно| SKIP3[Отбросить]
    end
```

**Почему два подхода?**
- **LLM-фильтр** — точный, понимает контекст ("продажа курсов по нейросетям" = реклама). Но медленный (1-2 сек). Используется при обработке постов (раз в 10-30 мин, до 30 постов).
- **Keyword-фильтр** — мгновенный, но грубый. Используется там, где важна скорость: генерация дайджеста (должна занимать секунды, не минуты) и фильтрация результатов Tavily.

---

## Расписание задач

| Задача | Интервал | Что делает |
|---|---|---|
| `task_parse_telegram` | Каждые 10 мин | Парсит все Telegram-каналы через Telethon, сохраняет новые посты, запускает обработку |
| `task_parse_web` | Каждые 30 мин | Парсит веб-источники (RSS/HTML), сохраняет новые посты, запускает обработку |
| `task_send_digests` | Каждую минуту | Проверяет, совпадает ли текущее время с `digest_time` пользователя, генерирует и отправляет дайджест |

---

## Потоки данных

```mermaid
flowchart LR
    subgraph input [Входные данные]
        TG[Telegram каналы]
        WEB[Веб-сайты]
    end

    subgraph process [Обработка]
        PARSE[Парсинг]
        ANALYZE[Анализ — DeepSeek + Embeddings]
        CLUSTER[Кластеризация похожих]
        ENRICH[Обогащение — Tavily]
    end

    subgraph output [Выходные данные]
        ALERT_OUT[Алерты в Telegram]
        DIGEST_OUT[Дайджест в Telegram]
        DISCOVER[Новые источники]
    end

    TG --> PARSE
    WEB --> PARSE
    PARSE --> ANALYZE
    ANALYZE --> CLUSTER
    CLUSTER --> ENRICH
    ENRICH --> ALERT_OUT
    ANALYZE --> DIGEST_OUT
    ALERT_OUT -.->|Кнопка: Найти ещё| DISCOVER
    DIGEST_OUT -.->|Кнопка: Найти источники| DISCOVER
    DISCOVER -.->|Подписка| PARSE
```
