from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    groq_api_key: str = ""
    usda_api_key: str = ""
    # Groq retires model IDs periodically — override with GROQ_MODEL if this one goes.
    groq_model: str = "openai/gpt-oss-120b"
    app_env: str = "development"
    database_url: str = "sqlite:///./data/desimacros.db"

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
