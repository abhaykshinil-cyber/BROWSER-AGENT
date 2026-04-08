"""
BrowserAgent Configuration Module

Centralised configuration for the BrowserAgent server.
Values are loaded from environment variables with sensible defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentConfig:
    """Immutable configuration container for the BrowserAgent server.

    All values fall back to defaults when the corresponding environment
    variable is not set.  The config is frozen so it can be safely shared
    across async tasks without locking.
    """

    # ── LLM Settings ──────────────────────────────────────────────────
    API_KEY: str = field(
        default_factory=lambda: (
            os.getenv("GEMINI_API_KEY")
            or os.getenv("BROWSERAGENT_API_KEY", "")
        ),
        metadata={"help": "Google Gemini API key (AIza…)."},
    )
    MODEL: str = field(
        default_factory=lambda: os.getenv("BROWSERAGENT_MODEL", "gemini-2.0-flash"),
        metadata={"help": "Gemini model identifier, e.g. gemini-2.0-flash."},
    )
    EMBEDDING_MODEL: str = field(
        default_factory=lambda: os.getenv(
            "BROWSERAGENT_EMBEDDING_MODEL", "models/text-embedding-004"
        ),
        metadata={"help": "Gemini embedding model for vector search."},
    )
    MAX_TOKENS: int = field(
        default_factory=lambda: int(os.getenv("BROWSERAGENT_MAX_TOKENS", "4096")),
        metadata={"help": "Maximum tokens the LLM may generate per request."},
    )

    # ── Server Settings ───────────────────────────────────────────────
    SERVER_PORT: int = field(
        default_factory=lambda: int(os.getenv("BROWSERAGENT_PORT", "8765")),
        metadata={"help": "Port the FastAPI / WebSocket server binds to."},
    )
    DEBUG: bool = field(
        default_factory=lambda: os.getenv("BROWSERAGENT_DEBUG", "false").lower()
        in ("1", "true", "yes"),
        metadata={"help": "Enable verbose logging and auto-reload."},
    )

    # ── Database / Storage ────────────────────────────────────────────
    DB_PATH: str = field(
        default_factory=lambda: os.getenv(
            "BROWSERAGENT_DB_PATH", "./database/agent.db"
        ),
        metadata={"help": "Path to the SQLite database file."},
    )

    # ── Execution Behaviour ───────────────────────────────────────────
    ACTION_DELAY_MS: int = field(
        default_factory=lambda: int(
            os.getenv("BROWSERAGENT_ACTION_DELAY_MS", "800")
        ),
        metadata={
            "help": (
                "Milliseconds to wait between successive browser actions. "
                "Prevents anti-bot detection and allows pages to settle."
            )
        },
    )

    # ── Convenience helpers ───────────────────────────────────────────
    @property
    def action_delay_seconds(self) -> float:
        """Return the action delay converted to seconds."""
        return self.ACTION_DELAY_MS / 1000.0

    def __post_init__(self) -> None:
        """Validate configuration after initialisation."""
        if self.SERVER_PORT < 1 or self.SERVER_PORT > 65535:
            raise ValueError(
                f"SERVER_PORT must be 1–65535, got {self.SERVER_PORT}"
            )
        if self.MAX_TOKENS < 1:
            raise ValueError(
                f"MAX_TOKENS must be positive, got {self.MAX_TOKENS}"
            )
        if self.ACTION_DELAY_MS < 0:
            raise ValueError(
                f"ACTION_DELAY_MS must be non-negative, got {self.ACTION_DELAY_MS}"
            )


def load_config() -> AgentConfig:
    """Create and return a validated AgentConfig from the environment.

    Call this once at application startup and pass the instance around
    via dependency injection (e.g. FastAPI ``Depends``).

    Returns:
        AgentConfig: Frozen configuration instance.
    """
    return AgentConfig()
