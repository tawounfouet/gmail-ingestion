"""Configuration models for Gmail authentication."""

from pathlib import Path

from pydantic import BaseModel, Field, field_validator

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


def default_credential_dir() -> Path:
    """Return the default private credential directory for local development."""
    return Path.home() / ".local" / "share" / "gmail-ingestion" / "credentials"


class OAuthSettings(BaseModel):
    """OAuth settings for the local Desktop flow used by milestone 0.1.0a1."""

    client_secrets_file: Path = Path("credentials.json")
    credential_dir: Path = Field(default_factory=default_credential_dir)
    scopes: tuple[str, ...] = (GMAIL_READONLY_SCOPE,)
    local_server_host: str = "localhost"
    local_server_port: int = 0
    open_browser: bool = True

    @field_validator("scopes")
    @classmethod
    def require_readonly_only(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject scope escalation in the first Gmail milestone."""
        if value != (GMAIL_READONLY_SCOPE,):
            raise ValueError("0.1.0a1 requires exactly the gmail.readonly scope")
        return value
