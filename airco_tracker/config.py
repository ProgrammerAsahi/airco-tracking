from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .azure_auth import default_azure_credential
from .i18n import SUPPORTED_LANGS, supported_lang


# The installed package lives inside .venv/site-packages. Runtime data belongs
# to the project working directory (the LaunchAgent sets it explicitly).
ROOT = Path(os.getenv("AIRCO_TRACKER_HOME", os.getcwd())).expanduser().resolve()
LOG = logging.getLogger(__name__)


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Load a small, dependency-free subset of dotenv syntax."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value[:1] == value[-1:] and value.startswith(("'", '"')):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _optional_float(name: str, default: str = "") -> float | None:
    value = os.getenv(name, default).strip()
    return float(value) if value else None


def _optional_int(name: str, default: str = "") -> int | None:
    value = os.getenv(name, default).strip()
    return int(value) if value else None


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _country_list(name: str, default: str) -> list[str]:
    """Parse a comma-separated country-code list into a lowercased, deduped list."""
    raw = os.getenv(name, default).strip()
    if not raw:
        return [c.strip().lower() for c in default.split(",") if c.strip()]
    countries: list[str] = []
    for code in raw.split(","):
        code = code.strip().lower()
        if code and code not in countries:
            countries.append(code)
    return countries or [default.strip().lower()]


@dataclass(frozen=True)
class Config:
    app_env: str
    email_backend: str
    email_to: str
    email_from: str
    email_lang: str
    smtp_host: str
    smtp_port: int
    smtp_security: str
    smtp_username: str
    smtp_password: str
    max_price_eur: float | None
    min_btu: int | None
    alert_on_first_seen: bool
    request_timeout_seconds: int
    countries: list[str]
    state_backend: str
    state_path: Path
    inventory_path: Path
    azure_storage_account_url: str
    azure_storage_container: str
    azure_storage_blob: str
    azure_inventory_blob: str
    acs_endpoint: str
    azure_key_vault_url: str
    auth_users_table: str

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        _load_key_vault_secrets()
        return cls(
            app_env=os.getenv("APP_ENV", "local").strip().lower(),
            email_backend=os.getenv("EMAIL_BACKEND", "smtp").strip().lower(),
            email_to=os.getenv("EMAIL_TO", "").strip(),
            email_from=os.getenv("EMAIL_FROM", "").strip(),
            email_lang=os.getenv("EMAIL_LANG", "zh").strip().lower(),
            smtp_host=os.getenv("SMTP_HOST", "").strip(),
            smtp_port=int(os.getenv("SMTP_PORT", "465")),
            smtp_security=os.getenv("SMTP_SECURITY", "ssl").strip().lower(),
            smtp_username=os.getenv("SMTP_USERNAME", "").strip(),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            max_price_eur=_optional_float("MAX_PRICE_EUR", "1500"),
            min_btu=_optional_int("MIN_BTU", "7000"),
            alert_on_first_seen=_bool("ALERT_ON_FIRST_SEEN", True),
            request_timeout_seconds=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "25")),
            countries=_country_list("COUNTRIES", "nl"),
            state_backend=os.getenv("STATE_BACKEND", "local").strip().lower(),
            state_path=ROOT / "state.json",
            inventory_path=ROOT / "inventory.json",
            azure_storage_account_url=os.getenv("AZURE_STORAGE_ACCOUNT_URL", "").strip(),
            azure_storage_container=os.getenv("AZURE_STORAGE_CONTAINER", "airco-tracker").strip(),
            azure_storage_blob=os.getenv("AZURE_STORAGE_BLOB", "state.json").strip(),
            azure_inventory_blob=os.getenv("AZURE_INVENTORY_BLOB", "inventory.json").strip(),
            acs_endpoint=os.getenv("ACS_ENDPOINT", "").strip(),
            azure_key_vault_url=os.getenv("AZURE_KEY_VAULT_URL", "").strip(),
            auth_users_table=os.getenv("AUTH_USERS_TABLE", "users").strip() or "users",
        )

    def validate_email(self) -> None:
        if not supported_lang(self.email_lang):
            raise ValueError(
                f"EMAIL_LANG must be one of {', '.join(SUPPORTED_LANGS)} (got {self.email_lang!r})"
            )
        if self.email_backend == "azure_communication":
            missing = [
                name
                for name, value in {
                    "EMAIL_TO": self.email_to,
                    "EMAIL_FROM": self.email_from,
                    "ACS_ENDPOINT": self.acs_endpoint,
                }.items()
                if not value
            ]
            if missing:
                raise ValueError("Missing Azure email configuration: " + ", ".join(missing))
            return
        if self.email_backend != "smtp":
            raise ValueError("EMAIL_BACKEND must be smtp or azure_communication")
        missing = [
            name
            for name, value in {
                "EMAIL_TO": self.email_to,
                "EMAIL_FROM": self.email_from,
                "SMTP_HOST": self.smtp_host,
            }.items()
            if not value
        ]
        if self.smtp_username and not self.smtp_password:
            missing.append("SMTP_PASSWORD")
        if self.smtp_security not in {"ssl", "starttls", "plain"}:
            raise ValueError("SMTP_SECURITY must be ssl, starttls, or plain")
        if missing:
            raise ValueError("Missing email configuration: " + ", ".join(missing))

    def validate_state(self) -> None:
        if self.state_backend == "local":
            return
        if self.state_backend != "azure_blob":
            raise ValueError("STATE_BACKEND must be local or azure_blob")
        if not self.azure_storage_account_url:
            raise ValueError("AZURE_STORAGE_ACCOUNT_URL is required for azure_blob state")

def _load_key_vault_secrets() -> None:
    """Optionally hydrate named environment variables from Key Vault.

    KEY_VAULT_SECRET_MAP uses ENV_NAME=secret-name pairs separated by commas.
    Existing environment values win, which keeps local development predictable.
    """
    vault_url = os.getenv("AZURE_KEY_VAULT_URL", "").strip()
    mapping = os.getenv("KEY_VAULT_SECRET_MAP", "").strip()
    if not vault_url or not mapping:
        return
    try:
        from azure.keyvault.secrets import SecretClient
    except ImportError as exc:
        raise RuntimeError("Install the 'azure' extra to use Azure Key Vault") from exc

    client = SecretClient(vault_url=vault_url, credential=default_azure_credential())
    for item in mapping.split(","):
        if "=" not in item:
            raise ValueError("KEY_VAULT_SECRET_MAP must contain ENV_NAME=secret-name pairs")
        env_name, secret_name = (part.strip() for part in item.split("=", 1))
        if env_name and secret_name and not os.getenv(env_name):
            try:
                os.environ[env_name] = client.get_secret(secret_name).value
            except Exception as exc:
                LOG.warning("Cannot load Key Vault secret %s: %s", secret_name, exc)
