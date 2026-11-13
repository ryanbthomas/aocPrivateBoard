import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Process = Literal["web", "worker", "migration"]


class ConfigurationError(ValueError):
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    app_env: Literal["dev", "prod"] = "dev"
    database_url: str = "sqlite:///./data-dev/dev.db"
    aoc_year: int = Field(default_factory=lambda: datetime.now(UTC).year, ge=2015)
    aoc_leaderboard_id: str | None = None
    aoc_fixtures: bool = False
    aoc_view_key: SecretStr | None = None
    github_client_id: str | None = None
    github_client_secret: SecretStr | None = None
    session_secret: SecretStr | None = None
    admin_github_login: str | None = None
    allowlist: str = ""
    upload_max_bytes: int = Field(default=1_048_576, gt=0)

    @property
    def allowlist_logins(self) -> tuple[str, ...]:
        return tuple(
            login.strip() for login in self.allowlist.split(",") if login.strip()
        )

    def resolve_aoc_session(self) -> SecretStr | None:
        session = os.environ.get("AOC_SESSION")
        if session and session.strip():
            return SecretStr(session.strip())

        if self.app_env == "dev":
            session = dotenv_values(".env").get("AOC_SESSION")
            if session and session.strip():
                return SecretStr(session.strip())

        credentials_directory = os.environ.get("CREDENTIALS_DIRECTORY")
        if credentials_directory:
            credential_path = Path(credentials_directory) / "aoc_session"
            try:
                session = credential_path.read_text(encoding="utf-8").strip()
            except OSError as error:
                raise ConfigurationError(
                    f"Unable to read AOC_SESSION credential at {credential_path}"
                ) from error
            if session:
                return SecretStr(session)

        return None

    def validate_for_process(self, process: Process) -> None:
        if self.app_env != "prod":
            return

        required: list[str] = []
        if process == "web":
            for name, value in (
                ("GITHUB_CLIENT_ID", self.github_client_id),
                ("GITHUB_CLIENT_SECRET", self.github_client_secret),
                ("SESSION_SECRET", self.session_secret),
            ):
                if value is None or (
                    isinstance(value, SecretStr) and not value.get_secret_value()
                ):
                    required.append(name)
        elif process == "worker":
            if not self.aoc_leaderboard_id:
                required.append("AOC_LEADERBOARD_ID")
            try:
                session = self.resolve_aoc_session()
            except ConfigurationError:
                raise
            if session is None:
                required.append("AOC_SESSION")

        if required:
            missing = ", ".join(required)
            raise ConfigurationError(
                f"Missing required production settings for {process}: {missing}"
            )


def load_settings(process: Process) -> Settings:
    settings = Settings()
    settings.validate_for_process(process)
    return settings
