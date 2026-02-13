from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Telegram Bot
    bot_token: str

    # Telegram Client (Telethon)
    telegram_api_id: int
    telegram_api_hash: str
    telegram_phone: str

    # PostgreSQL
    postgres_user: str = "tg_parser"
    postgres_password: str = "tg_parser_secret"
    postgres_db: str = "tg_parser_db"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # DeepSeek LLM
    deepseek_api_key: str
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    # Parsing intervals (minutes)
    telegram_parse_interval: int = 10
    web_parse_interval: int = 30
    api_source_max_items: int = 20
    api_source_lookback_hours: int = 48

    # Alerts
    similarity_threshold: float = 0.82
    reactions_multiplier: float = 3.0
    cluster_min_mentions: int = 2
    coreai_alert_threshold: float = 0.6

    # Tavily (web search for discovering new sources)
    tavily_api_key: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "telegram-ai-parser/1.0"
    github_api_key: str = ""
    producthunt_api_key: str = ""

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
