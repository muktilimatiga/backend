from pydantic_settings import BaseSettings
from typing import Optional, Dict

class Settings(BaseSettings):
    # --- PostgreSQL Database ---
    DB_HOST: str
    DB_PORT: int
    DB_NAME: str
    DB_USER: str
    DB_PASS: str

    # --- Credentials ---
    OLT_USERNAME: str
    OLT_PASSWORD: str
    NMS_USERNAME: str
    NMS_PASSWORD: str
    NMS_USERNAME_BILING: str
    NMS_PASSWORD_BILING: str
    DATA_PSB_URL: str

    # --- Services ---
    TELNET_TIMEOUT: int = 15
    BOT_TOKEN: str
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7
    
    # --- Development Mode ---
    DISABLE_AUTH: bool = False  # Set to True to disable JWT authentication for development

    # --- URLs ---
    LOGIN_URL: str
    LOGIN_URL_BILLING: str
    DETAIL_URL_BILLING: str
    BILLING_MODULE_BASE: str
    TICKET_NOC_URL: str
    DATA_PSB_URL: str

    class Config:
        env_file = ".env"
        env_file_encoding = 'utf-8'
        extra = 'ignore'

settings = Settings()
