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
    # NB: this exact string is the *sentinel* default. Lifespan-time
    # check rejects starting the server with this value unless
    # ``allow_default_secret`` is explicitly true. Anything else is
    # treated as user-configured and accepted.
    secret_key: str = Field(
        default="change-me-in-production-use-a-real-secret-key",
        description="JWT secret key",
    )
    allow_default_secret: bool = Field(
        default=False,
        description=(
            "Permit booting with the sentinel default secret_key. Off by "
            "default — production deployments MUST set BLPREMOTE_SECRET_KEY "
            "to something distinct, otherwise the server refuses to start. "
            "Set BLPREMOTE_ALLOW_DEFAULT_SECRET=true for local dev, with "
            "the understanding that all tokens minted with the default "
            "key are forgeable by anyone reading the source."
        ),
    )
    rotate_secret_at_boot: bool = Field(
        default=False,
        description=(
            "Generate a fresh random secret_key at every boot and discard "
            "it on shutdown. Useful for ephemeral deployments where token "
            "longevity beyond the current process is undesirable. Mutually "
            "exclusive with allow_default_secret (rotation overrides). "
            "Implication: every restart invalidates all outstanding tokens."
        ),
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

    # M4: observability + audit
    log_format: str = Field(
        default="text",
        description="'text' (dev-friendly) or 'json' (structured for log shippers)",
    )
    log_level: str = Field(
        default="INFO",
        description="Root log level (DEBUG/INFO/WARNING/ERROR)",
    )
    audit_log_path: str = Field(
        default="",
        description=(
            "Path to JSONL audit log. Empty = audit to the structured logger "
            "instead (one entry per executed plan). Set to e.g. './audit.jsonl' "
            "to also tee to a dedicated file for offline analysis."
        ),
    )
    audit_include_raw_ir: bool = Field(
        default=False,
        description=(
            "Include the raw IR ops list on every audit entry. Off by default "
            "because lines bloat past 1KB on big plans. ir_hash is always "
            "included so plans correlate even without the raw payload."
        ),
    )
    request_cache_max_entries: int = Field(
        default=1024,
        description=(
            "Maximum entries in the in-process request cache (LRU eviction). "
            "Each entry is a small ExecutionResult; 1024 fits comfortably in "
            "a few MB even for big response payloads."
        ),
    )
    request_cache_ttl_s: float = Field(
        default=5.0,
        description=(
            "Per-entry TTL for the request cache, in seconds. Default is "
            "intentionally short — live BBG fields move within seconds, so a "
            "wide TTL would serve stale prices. Set to 0 to disable caching "
            "entirely. Plans with absolute timestamps (HistoricalDataRequest, "
            "fixed-window IntradayBarRequest) hash identically across calls "
            "and benefit from the cache. Plans with rolling 'last N minutes' "
            "windows produce a fresh hash each call and never hit the cache."
        ),
    )
    # M5 (B): per-user rate limits via in-memory token bucket. The
    # buckets refill at a steady rate; bursts of up to `bucket_size`
    # are absorbed before any caller hits 429. Defaults are generous
    # for a single-team deployment and intentionally NOT zero —
    # zero on either field disables rate limiting for that bound.
    rate_limit_per_minute: int = Field(
        default=300,
        description=(
            "Maximum /v1/execute calls per user per 60s rolling window. "
            "0 disables. Surfaces as HTTP 429 + RATE_LIMITED status in "
            "/metrics when exceeded. Tune per deployment — default 300 "
            "covers a normal interactive session with headroom."
        ),
    )
    rate_limit_burst: int = Field(
        default=30,
        description=(
            "Burst allowance — number of requests a user can fire in a "
            "tight loop before throttling kicks in. Refills at "
            "rate_limit_per_minute / 60 per second. 0 disables. Default "
            "30 absorbs a small parallel fan-out (e.g. ten symbols × "
            "three field families) without 429s."
        ),
    )

    model_config = {
        "env_prefix": "BLPREMOTE_",
        "env_file": ".env",
        "extra": "ignore",
    }


# Global settings instance
settings = Settings()
