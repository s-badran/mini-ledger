from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Read from LEDGER_* env vars. Everything has a working default for local dev."""

    model_config = SettingsConfigDict(env_prefix="LEDGER_")

    database_url: str = "postgresql+psycopg://ledger:ledger@localhost:5433/ledger"
    echo_sql: bool = False

    @field_validator("database_url")
    @classmethod
    def _require_postgres(cls, value: str) -> str:
        # I keep seeing people point this at SQLite while debugging, then wonder
        # why the row locks silently do nothing. Fail early instead.
        if not value.startswith("postgresql"):
            raise ValueError("mini-ledger needs Postgres; SQLite does not do SELECT ... FOR UPDATE")
        return value


settings = Settings()
