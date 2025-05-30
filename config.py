from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    telegram_api_id: str
    telegram_api_hash: str
    telegram_bot_token: str
    google_credentials_path: str
    google_sheet_id: str
    redis_url: str = ""
    # Добавляйте свои параметры ниже

    class Config:
        env_file = ".env"

settings = Settings()
