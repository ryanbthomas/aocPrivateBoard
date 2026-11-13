import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx2
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.config import ConfigurationError, Settings
from app.models import AuditLog, Fetch

logger = logging.getLogger(__name__)
LEADERBOARD_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"

USER_AGENT = "aocPrivateBoard/0.1 (+https://github.com/ryanbthomas/aocPrivateBoard)"
CACHE_TTL = timedelta(seconds=900)
REQUEST_TIMEOUT = 20.0
MAX_ATTEMPTS = 3
MAX_RETRY_AFTER = 20.0
LOGIN_MARKERS = (
    "please log in",
    "log in to get your puzzle input",
    "log in to get your puzzle",
    'name="username"',
)
_URL_LOCKS_GUARD = threading.Lock()
_URL_LOCKS: dict[str, threading.Lock] = {}


class AoCError(RuntimeError):
    pass


class SessionExpired(AoCError):
    pass


class AoCFetchClient:
    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        *,
        client: httpx2.Client | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._client = client or httpx2.Client(timeout=REQUEST_TIMEOUT)
        self._owns_client = client is None
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep
        self._reported_incidents: set[tuple[str, str]] = set()
        self._session_expired_reported = False
        self._session_cookie_set = False

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> AoCFetchClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get_json(self, url: str, *, fixture_key: str | None = None) -> dict[str, Any]:
        self._validate_leaderboard_url(url)
        if self._settings.aoc_fixtures:
            try:
                return self._load_leaderboard_fixture(url)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                raise AoCError(
                    "Unable to load the configured leaderboard fixture"
                ) from error

        lock = self._get_url_lock(url)
        with lock:
            now = self._now()
            cached = self._read_cache(url)
            if cached is not None and self._is_fresh(cached, now):
                return self._decode_payload(cached.payload)
            return self._fetch_and_cache(url, cached)

    def _fetch_and_cache(self, url: str, cached: Fetch | None) -> dict[str, Any]:
        session = self._settings.resolve_aoc_session()
        if session is None:
            raise ConfigurationError("AOC_SESSION is required for live AoC requests")

        headers = {"User-Agent": USER_AGENT}
        if cached is not None and cached.etag:
            headers["If-None-Match"] = cached.etag
        if cached is not None and cached.last_modified:
            headers["If-Modified-Since"] = cached.last_modified

        response = self._request(url, headers, session.get_secret_value())
        if response.status_code == 304:
            if cached is None or cached.payload is None:
                raise AoCError("AoC returned 304 without a cached leaderboard payload")
            cached.fetched_at = self._now().isoformat()
            with Session(self._engine) as db:
                db.merge(cached)
                db.commit()
            self._clear_incident(url, "request")
            self._clear_session_incident()
            return self._decode_payload(cached.payload)

        body = response.text
        if self._is_expired_session(response.status_code, body):
            self._record_session_expiry(url, response.status_code)
            raise SessionExpired("AoC session expired; refresh the worker credential")

        if not 200 <= response.status_code < 300:
            self._report_once(
                (url, "http"),
                "AoC request failed",
                url=url,
                status_code=response.status_code,
            )
            raise AoCError(f"AoC returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as error:
            self._report_once(
                (url, "json"),
                "AoC returned invalid JSON",
                url=url,
                status_code=response.status_code,
            )
            raise AoCError("AoC returned invalid JSON") from error

        if not isinstance(payload, dict):
            raise AoCError("AoC leaderboard response must be a JSON object")

        self._store_response(url, payload, response)
        self._clear_incident(url, "request")
        self._clear_incident(url, "json")
        self._clear_session_incident()
        return payload

    def _request(
        self, url: str, headers: dict[str, str], session_cookie: str
    ) -> httpx2.Response:
        if not self._session_cookie_set:
            self._client.cookies.set(
                "session",
                session_cookie,
                domain="adventofcode.com",
                path="/",
            )
            self._session_cookie_set = True
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = self._client.get(
                    url,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                    follow_redirects=False,
                )
            except httpx2.RequestError as error:
                if attempt + 1 == MAX_ATTEMPTS:
                    self._report_once(
                        (url, "request"),
                        "AoC request exhausted retries",
                        url=url,
                        error_type=type(error).__name__,
                    )
                    raise AoCError("AoC request failed after retries") from error
                self._sleep(0.5 * (2**attempt))
                continue

            if (
                response.status_code < 500 and response.status_code != 429
            ) or attempt + 1 == MAX_ATTEMPTS:
                return response

            delay = (
                self._retry_after(response)
                if response.status_code == 429
                else 0.5 * (2**attempt)
            )
            self._sleep(delay)

        raise AoCError("AoC request failed after retries")

    def _store_response(
        self, url: str, payload: dict[str, Any], response: httpx2.Response
    ) -> None:
        cache_row = Fetch(
            url=url,
            payload=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
            fetched_at=self._now().isoformat(),
        )
        with Session(self._engine) as db:
            db.merge(cache_row)
            db.commit()

    def _read_cache(self, url: str) -> Fetch | None:
        with Session(self._engine) as db:
            return db.scalar(select(Fetch).where(Fetch.url == url))

    def _is_fresh(self, cached: Fetch, now: datetime) -> bool:
        if cached.payload is None:
            return False
        try:
            fetched_at = datetime.fromisoformat(cached.fetched_at)
        except ValueError as error:
            raise AoCError("Stored AoC cache timestamp is invalid") from error
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=UTC)
        return now - fetched_at < CACHE_TTL

    def _decode_payload(self, payload: bytes | None) -> dict[str, Any]:
        if payload is None:
            raise AoCError("Cached AoC response has no payload")
        try:
            decoded = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise AoCError("Cached AoC response contains invalid JSON") from error
        if not isinstance(decoded, dict):
            raise AoCError("Cached AoC response must be a JSON object")
        return decoded

    def _is_expired_session(self, status_code: int, body: str) -> bool:
        if status_code in (302, 401, 403):
            return True
        body_lower = body.lower()
        return any(marker in body_lower for marker in LOGIN_MARKERS)

    def _retry_after(self, response: httpx2.Response) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after is None:
            return 1.0
        try:
            delay = float(retry_after)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
            except TypeError, ValueError:
                return 1.0
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            delay = (retry_at - self._now()).total_seconds()
        return min(MAX_RETRY_AFTER, max(0.0, delay))

    def _record_session_expiry(self, url: str, status_code: int) -> None:
        if self._session_expired_reported:
            return
        with Session(self._engine) as db:
            db.add(
                AuditLog(
                    at=self._now().isoformat(),
                    actor_user_id=None,
                    action="aoc.session_expired",
                    target=url,
                    detail=f"HTTP {status_code}",
                )
            )
            db.commit()
        self._session_expired_reported = True
        self._report_once(
            ("aoc", "session_expired"),
            "AoC session expired",
            url=url,
            status_code=status_code,
        )

    def _load_leaderboard_fixture(self, url: str) -> dict[str, Any]:
        path_parts = urlparse(url).path.strip("/").split("/")
        year = path_parts[0] if path_parts else ""
        if len(year) != 4 or not year.isdecimal():
            raise ValueError(f"Unable to determine AoC year from URL: {url}")

        fixture_path = LEADERBOARD_FIXTURES / f"leaderboard_{year}.json"
        with fixture_path.open(encoding="utf-8") as fixture:
            payload = json.load(fixture)
        if not isinstance(payload, dict):
            raise AoCError("Leaderboard fixture must be a JSON object")
        return payload

    def _report_once(
        self, incident: tuple[str, str], message: str, **details: object
    ) -> None:
        if incident in self._reported_incidents:
            return
        self._reported_incidents.add(incident)
        logger.warning(message, extra={"aoc": details})

    def _clear_incident(self, url: str, category: str) -> None:
        self._reported_incidents.discard((url, category))

    def _clear_session_incident(self) -> None:
        self._session_expired_reported = False
        self._reported_incidents.discard(("aoc", "session_expired"))

    def _get_url_lock(self, url: str) -> threading.Lock:
        with _URL_LOCKS_GUARD:
            return _URL_LOCKS.setdefault(url, threading.Lock())

    def _validate_leaderboard_url(self, url: str) -> None:
        parsed = urlparse(url)
        parts = parsed.path.strip("/").split("/")
        if (
            parsed.scheme != "https"
            or parsed.netloc != "adventofcode.com"
            or parsed.query
            or parsed.fragment
            or len(parts) != 5
            or len(parts[0]) != 4
            or not parts[0].isdecimal()
            or parts[1:4] != ["leaderboard", "private", "view"]
            or not parts[4].removesuffix(".json")
            or not parts[4].endswith(".json")
        ):
            raise ValueError(
                "AoC fetch client only accepts private leaderboard JSON URLs"
            )

    def _now(self) -> datetime:
        now = self._clock()
        return now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
