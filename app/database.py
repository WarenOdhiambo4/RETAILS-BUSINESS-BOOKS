
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine


class DatabaseSettings(BaseSettings):
    server: str
    database: str
    username: str
    password: str
    driver: str = "ODBC Driver 17 for SQL Server"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="SQL_",
        extra="ignore",
    )


settings = DatabaseSettings()

connection_url = URL.create(
    drivername="mssql+pyodbc",
    username=settings.username,
    password=settings.password,
    host=settings.server,
    database=settings.database,
    query={
        "driver": settings.driver,
        "Encrypt": "yes",
        "TrustServerCertificate": "yes",
    },
)

engine: Engine = create_engine(
    connection_url,
    pool_pre_ping=True,
    pool_size=2,
    max_overflow=0,
    pool_recycle=1800,
    echo=False,
)
