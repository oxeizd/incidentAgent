from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    LLM_PROVIDER: str = "gigachat"
    LLM_TEMPERATURE: float = 0.1
    LLM_MAX_TOKENS: int = 4000
    GIGACHAT_BASE_URL: Optional[str] = None
    GIGACHAT_SCOPE: str = "GIGACHAT_API_B2B"
    GIGACHAT_MODEL: str = "GigaChat-3-Ultra"
    GIGACHAT_TIMEOUT: int = 120
    LOG_LEVEL: str = "INFO"
    PHOENIX_ENABLED: bool = True
    CA_CERT_PATH: str = "data/certs/NT/ca.pem"
    CLIENT_CERT_PATH: str = "data/certs/NT/cert.pem"
    CLIENT_KEY_PATH: str = "data/certs/NT/private.key"
    ENTITY_CATALOG_PATH: str = "data/entity_catalog.json"

    class Config:
        env_file = ".env"
        extra = "ignore"

    WORKER_MODEL_RCA: Optional[str] = None
    WORKER_MODEL_ANALYZER: Optional[str] = None
    WORKER_MODEL_VALIDATOR: Optional[str] = None
    WORKER_MODEL_SEARCH: Optional[str] = None
    WORKER_MODEL_SUPERVISOR: Optional[str] = None
    WORKER_MODEL_EDITOR: Optional[str] = None


settings = Settings()


@dataclass(frozen=True)
class MemorySettings:
    sqlite_vec_enabled: bool = True
    vector_dim: int = 312
    auto_backfill_vectors: bool = False
    embeddings_model_path: str = "data/models/sergeyzh_rubert-mini-frida/sergeyzh:rubert-mini-frida"
    result_ttl_hours: int = 24
    max_saved_items: int = 2000
    incidents_path: Path = Path("data/seed/incidents.json")
    assignments_path: Path = Path("data/seed/assignments.json")
    schema_path: Path = Path("app/memory/db/schemas/schema.sql")
    migrations_path: Path = Path("app/memory/db/migrations")
    db_path: Path = Path("data/runtime/memory.sqlite3")
    search_output_config_path: Path = Path("data/config/search_output.yaml")


memory_settings = MemorySettings()