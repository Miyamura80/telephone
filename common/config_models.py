"""
Pydantic models for global configuration structure.

This module defines all the nested configuration models used by the Config class.
Each model corresponds to a section in the global_config.yaml file and provides
type validation and structure for the configuration data.
"""

from typing import Literal

from pydantic import BaseModel, Field, NonNegativeFloat


class ExampleParent(BaseModel):
    """Example configuration parent model."""

    example_child: str


class DefaultLlm(BaseModel):
    """Default LLM configuration."""

    default_model: str
    fallback_model: str | None = None
    default_temperature: float
    default_max_tokens: int


class RetryConfig(BaseModel):
    """Retry configuration for LLM requests."""

    max_attempts: int
    min_wait_seconds: int
    max_wait_seconds: int


class LlmConfig(BaseModel):
    """LLM configuration including caching and retry settings."""

    cache_enabled: bool
    retry: RetryConfig


class LoggingLocationConfig(BaseModel):
    """Location information display configuration for logging."""

    enabled: bool
    show_file: bool
    show_function: bool
    show_line: bool
    show_for_info: bool
    show_for_debug: bool
    show_for_warning: bool
    show_for_error: bool


class LoggingFormatConfig(BaseModel):
    """Logging format configuration."""

    show_time: bool
    show_session_id: bool
    location: LoggingLocationConfig


class LoggingLevelsConfig(BaseModel):
    """Logging level configuration."""

    debug: bool
    info: bool
    warning: bool
    error: bool
    critical: bool


class RedactionPattern(BaseModel):
    """Configuration for a specific redaction pattern."""

    name: str
    regex: str
    placeholder: str


class RedactionConfig(BaseModel):
    """Configuration for log redaction/scrubbing."""

    enabled: bool = True
    use_default_pii: bool = True
    patterns: list[RedactionPattern] = []


class LoggingConfig(BaseModel):
    """Complete logging configuration."""

    verbose: bool
    format: LoggingFormatConfig
    levels: LoggingLevelsConfig
    redaction: RedactionConfig = Field(default_factory=lambda: RedactionConfig())


class TelemetryConfig(BaseModel):
    """Telemetry configuration."""

    enabled: bool = True
    endpoint: str | None = None


class CliConfig(BaseModel):
    """CLI configuration."""

    default_format: str = "table"
    interactive_fallback: bool = True
    check_updates_on_start: bool = False
    emoji: str = ""
    primary_color: str = "cyan"
    secondary_color: str = "green"


class ServerConfig(BaseModel):
    """API server configuration."""

    host: str = "0.0.0.0"
    port: int = 8080
    allowed_origins: list[str] = ["http://localhost:3000"]
    # Streamable-HTTP session mode for the /mcp mount. True (default) keeps
    # every request self-contained - required for multi-replica / spin-down
    # deployments - but makes in-band elicitation impossible (the client's
    # elicitation response POST cannot be correlated back to the transient
    # session, so ctx.elicit() hangs; empirically verified, same failure mode
    # as python-sdk issue 678 for sampling). Single-replica deployments may
    # set False to enable host-native elicitation (e.g. the signing
    # confirmation dialog) at the cost of in-memory session affinity.
    mcp_stateless_http: bool = True


class IconConfig(BaseModel):
    """A sized icon for pre-connect branding (MCP icons spec).

    ``src`` must be an HTTPS URL. ``sizes`` accepts ``WxH`` entries or
    ``"any"`` (for SVG); ``theme`` is ``"light"``/``"dark"`` when an icon is
    tuned for one mode.
    """

    src: str
    mime_type: str = "image/svg+xml"
    sizes: list[str] = Field(default_factory=lambda: ["any"])
    theme: Literal["light", "dark"] | None = None


class BrandingConfig(BaseModel):
    """Pre-connect registry branding (SEP-2127 Server Card / MCP registry).

    Single source of truth for the server's public identity: the reverse-DNS
    ``name`` a registry indexes by, and the ``title``/``description``/``icons``
    a client shows in its "add server" UI *before* anyone connects. Surfaced
    verbatim at ``/.well-known/mcp/server-card.json``. The deployed MCP endpoint
    URL is not configured here - it is derived from ``MCP_PUBLIC_URL`` so the
    card and the OAuth resource metadata can never disagree.
    """

    name: str = "io.github.Miyamura80/telephone"
    title: str = "Telephone"
    description: str = (
        "Give your AI agent real tools - one service registry over CLI, MCP, and HTTP."
    )
    website_url: str = "https://github.com/Miyamura80/telephone"
    repository_url: str = "https://github.com/Miyamura80/telephone"
    repository_source: str = "github"
    icons: list[IconConfig] = Field(
        default_factory=lambda: [
            IconConfig(
                src="https://raw.githubusercontent.com/Miyamura80/telephone/main/landing-page/public/favicon.svg"
            )
        ]
    )


class WebBotAuthConfig(BaseModel):
    """Web Bot Auth signing-key directory configuration.

    Drives ``/.well-known/http-message-signatures-directory``
    (draft-meunier-http-message-signatures-directory), which publishes this
    agent's Ed25519 public signing key(s) as a JWK Set so origins can verify
    HTTP Message Signatures it sends. The private key is supplied out of band
    via the ``WEB_BOT_AUTH_PRIVATE_KEY`` secret (a base64url-encoded 32-byte
    Ed25519 seed); when that is unset the route returns 404, cleanly signalling
    "no signing identity" instead of advertising an empty directory.

    ``key_lifetime_days`` sets the per-key ``exp`` (relative to first publish);
    ``nbf`` is the publish time.
    """

    key_lifetime_days: int = Field(default=365, ge=1)


class RateLimitConfig(BaseModel):
    """Rate limiting configuration."""

    enabled: bool = True
    trust_proxy_headers: bool = False
    tiers: dict[str, dict[str, int]] = Field(
        default_factory=lambda: {
            "free_tier": {"rps": 2, "rpm": 30, "rph": 200, "rpd": 100},
            "plus_tier": {"rps": 10, "rpm": 120, "rph": 5000, "rpd": 10000},
            "default": {"rps": 5, "rpm": 60, "rph": 1000, "rpd": 5000},
        }
    )


class StripeConfig(BaseModel):
    """Stripe billing configuration."""

    price_ids: dict[str, str] = Field(default_factory=lambda: {"test": "", "prod": ""})
    meter_event_name: str = "api_requests"
    api_version: str = "2025-03-31.basil"


class MeteredConfig(BaseModel):
    """Metered billing configuration."""

    included_units: int = 100
    overage_unit_amount: int = 1
    unit_label: str = "API calls"


class PaymentRetryConfig(BaseModel):
    """Payment retry configuration."""

    max_attempts: int = 3


class TierLimitsConfig(BaseModel):
    """Tier limit configuration."""

    daily_requests: int = 100


class SubscriptionConfig(BaseModel):
    """Subscription/billing configuration."""

    tier_limits: dict[str, TierLimitsConfig] = Field(
        default_factory=lambda: {
            "free_tier": TierLimitsConfig(daily_requests=100),
            "plus_tier": TierLimitsConfig(daily_requests=10000),
        }
    )
    default_tier: str = "free_tier"
    stripe: StripeConfig = Field(default_factory=StripeConfig)
    metered: MeteredConfig = Field(default_factory=MeteredConfig)
    trial_period_days: int = 7
    payment_retry: PaymentRetryConfig = Field(default_factory=PaymentRetryConfig)


class X402ProtocolConfig(BaseModel):
    """x402 (Coinbase) protocol configuration."""

    enabled: bool = False
    facilitator_url: str = "https://x402.org/facilitator"
    network: str = "base-sepolia"
    wallet_address_env: str = "X402_WALLET_ADDRESS"
    private_key_env: str = "X402_PRIVATE_KEY"
    default_amount: str = "0.001"
    default_asset: str = "USDC"
    testnet: bool = True


class MppProtocolConfig(BaseModel):
    """MPP (Stripe/Tempo) protocol configuration."""

    enabled: bool = False


class AcpProtocolConfig(BaseModel):
    """ACP (OpenAI/Stripe) protocol configuration."""

    enabled: bool = False


class AgenticPaymentsConfig(BaseModel):
    """Top-level agentic payments configuration."""

    x402: X402ProtocolConfig = Field(default_factory=X402ProtocolConfig)
    mpp: MppProtocolConfig = Field(default_factory=MppProtocolConfig)
    acp: AcpProtocolConfig = Field(default_factory=AcpProtocolConfig)


class AskConfig(BaseModel):
    """NLWeb ``/ask`` Q&A endpoint configuration."""

    enabled: bool = False
    corpus_path: str = "docs/content/docs"
    docs_base_url: str = "https://docs.telephone.example.com"
    top_k: int = 5
    rate_limit_per_minute: int = 20


class FeaturesConfig(BaseModel):
    """Feature flags configuration."""

    model_config = {"extra": "allow"}  # Allow arbitrary flags


class TelephoneEventsConfig(BaseModel):
    """MCP Events (signed webhook) delivery settings for the switchboard."""

    allow_insecure_callbacks: bool = False
    default_ttl_s: int = Field(default=3600, ge=60)
    max_ttl_s: int = Field(default=604800, ge=60)
    max_attempts: int = Field(default=4, ge=1)
    backoff_s: list[NonNegativeFloat] = Field(
        default_factory=lambda: [1.0, 5.0, 30.0], min_length=1
    )
    request_timeout_s: float = Field(default=10.0, gt=0)


class TelephoneConfig(BaseModel):
    """Switchboard settings (loaded from telephone.yaml)."""

    default_wait_s: float = Field(default=50.0, ge=0)
    max_wait_s: float = Field(default=540.0, ge=0)
    heartbeat_s: float = Field(default=10.0, gt=0)
    max_turns: int = Field(default=60, ge=2)
    idle_timeout_s: float = Field(default=900.0, gt=0)
    transcript_dir: str | None = ".telephone/transcripts"
    events: TelephoneEventsConfig = Field(default_factory=TelephoneEventsConfig)
