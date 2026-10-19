
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class SecuritySettings(BaseSettings):
    verification_code_secret: SecretStr
    n8n_webhook_url: str
    n8n_webhook_secret: SecretStr
    n8n_password_reset_webhook_url: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


security_settings = SecuritySettings()
