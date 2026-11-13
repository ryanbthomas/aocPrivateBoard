from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.config import ConfigurationError, Settings, load_settings
from app.db import create_database_engine
from app.main import create_app


@pytest.fixture(autouse=True)
def isolate_settings_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    for name in (
        "APP_ENV",
        "DATABASE_URL",
        "AOC_YEAR",
        "AOC_LEADERBOARD_ID",
        "AOC_FIXTURES",
        "AOC_VIEW_KEY",
        "GITHUB_CLIENT_ID",
        "GITHUB_CLIENT_SECRET",
        "SESSION_SECRET",
        "ADMIN_GITHUB_LOGIN",
        "ALLOWLIST",
        "UPLOAD_MAX_BYTES",
        "AOC_SESSION",
        "CREDENTIALS_DIRECTORY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_development_settings_and_app_need_no_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings()
    app = create_app(settings)

    assert settings.app_env == "dev"
    assert settings.database_url == "sqlite:///./data-dev/dev.db"
    assert app.state.settings is settings


def test_settings_load_environment_values_and_parse_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AOC_FIXTURES", "1")
    monkeypatch.setenv("ALLOWLIST", "alice, bob ,,")
    monkeypatch.setenv("UPLOAD_MAX_BYTES", "2048")

    settings = Settings()

    assert settings.aoc_fixtures is True
    assert settings.allowlist_logins == ("alice", "bob")
    assert settings.upload_max_bytes == 2048


def test_settings_load_dotenv_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text(
        "DATABASE_URL=sqlite:///./from-dotenv.db\nAOC_FIXTURES=1\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AOC_FIXTURES", raising=False)

    settings = Settings()

    assert settings.database_url == "sqlite:///./from-dotenv.db"
    assert settings.aoc_fixtures is True
    assert create_database_engine().url.database == "./from-dotenv.db"


def test_worker_reads_systemd_credential_when_environment_is_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    credential_dir = tmp_path / "credentials"
    credential_dir.mkdir()
    (credential_dir / "aoc_session").write_text("worker-cookie\n", encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credential_dir))
    monkeypatch.delenv("AOC_SESSION", raising=False)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("AOC_LEADERBOARD_ID", "123")

    settings = load_settings("worker")
    session = settings.resolve_aoc_session()

    assert isinstance(session, SecretStr)
    assert session.get_secret_value() == "worker-cookie"
    assert "worker-cookie" not in repr(settings)
    assert "worker-cookie" not in repr(session)


def test_environment_session_takes_precedence_over_systemd_credential(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    credential_dir = tmp_path / "credentials"
    credential_dir.mkdir()
    (credential_dir / "aoc_session").write_text("file-cookie", encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credential_dir))
    monkeypatch.setenv("AOC_SESSION", "environment-cookie")

    session = Settings().resolve_aoc_session()

    assert session is not None
    assert session.get_secret_value() == "environment-cookie"
    assert "environment-cookie" not in repr(session)


def test_development_session_can_be_read_from_dotenv_only_by_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("AOC_SESSION=local-cookie\n", encoding="utf-8")
    monkeypatch.delenv("AOC_SESSION", raising=False)

    session = Settings().resolve_aoc_session()

    assert session is not None
    assert session.get_secret_value() == "local-cookie"
    assert "local-cookie" not in repr(session)


def test_production_web_requires_oauth_and_cookie_signing_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "must-not-appear")

    with pytest.raises(ConfigurationError) as error:
        create_app()

    assert "GITHUB_CLIENT_ID" in str(error.value)
    assert "SESSION_SECRET" in str(error.value)
    assert "must-not-appear" not in str(error.value)


def test_production_worker_requires_leaderboard_and_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AOC_SESSION", raising=False)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.setenv("APP_ENV", "prod")
    with pytest.raises(ConfigurationError) as error:
        load_settings("worker")

    assert str(error.value) == (
        "Missing required production settings for worker: "
        "AOC_LEADERBOARD_ID, AOC_SESSION"
    )


def test_web_validation_does_not_read_worker_credential_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    credential_dir = tmp_path / "missing-credentials"
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credential_dir))
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "client-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("SESSION_SECRET", "a-long-enough-secret")
    load_settings("web")


def test_invalid_app_environment_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    with pytest.raises(ValidationError):
        Settings()


def test_credential_read_error_does_not_expose_cookie_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    credential_dir = tmp_path / "credentials"
    credential_dir.mkdir()
    (credential_dir / "aoc_session").mkdir()
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credential_dir))
    monkeypatch.delenv("AOC_SESSION", raising=False)
    monkeypatch.setenv("APP_ENV", "prod")

    with pytest.raises(ConfigurationError, match="Unable to read AOC_SESSION"):
        Settings().validate_for_process("worker")
