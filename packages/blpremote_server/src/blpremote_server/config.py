"""Server configuration using Pydantic settings."""

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Server configuration loaded from environment variables."""

    # Server
    host: str = Field(default="0.0.0.0", description="Server host")
    port: int = Field(default=8000, description="Server port")
    debug: bool = Field(default=False, description="Debug mode")

    # Authentication
    secret_key: str = Field(
        default="change-me-in-production-use-a-real-secret-key",
        description="JWT secret key",
    )
    token_expire_minutes: int = Field(default=60, description="Token expiration in minutes")
    algorithm: str = Field(default="HS256", description="JWT algorithm")

    # User store path (simple JSON file for MVP)
    user_store_path: str = Field(default="users.json", description="Path to user store file")

    # Bloomberg
    bloomberg_server_host: str = Field(default="localhost", description="Bloomberg API server")
    bloomberg_server_port: int = Field(default=8194, description="Bloomberg API port")

    # Limits
    max_securities: int = Field(default=200, description="Maximum securities per request")
    max_fields: int = Field(default=200, description="Maximum fields per request")
    max_timeout_ms: int = Field(default=120000, description="Maximum timeout in milliseconds")

    # Allowed services and request types
    allowed_services: list[str] = Field(
        default=["//blp/refdata", "//blp/apiflds"],
        description="Allowed Bloomberg services",
    )
    allowed_request_types: list[str] = Field(
        default=[
            "ReferenceDataRequest",
            "HistoricalDataRequest",
            "IntradayBarRequest",
            "IntradayTickRequest",
            "FieldInfoRequest",
        ],
        description="Allowed request types",
    )

    # IP allowlist (empty = allow all)
    ip_allowlist: list[str] = Field(
        default=[],
        description="Allowed IP addresses (empty = allow all)",
    )

    model_config = {
        "env_prefix": "BLPREMOTE_",
        "env_file": ".env",
        "extra": "ignore",
    }


# Global settings instance
settings = Settings()
