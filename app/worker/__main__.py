import argparse
import logging
from collections.abc import Sequence

from app.aoc.client import AoCFetchClient
from app.aoc.poller import run_poll_cycle
from app.config import load_settings
from app.db import create_database_engine
from app.worker.scheduler import run_forever

logger = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the AoC leaderboard poller")
    parser.add_argument(
        "--once",
        action="store_true",
        help="poll once and exit instead of starting the scheduler",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    try:
        settings = load_settings("worker")
        engine = create_database_engine(settings.database_url)
    except Exception as error:
        logger.error(
            "Unable to initialize AoC worker",
            extra={"error_type": type(error).__name__},
        )
        return 1

    try:
        with AoCFetchClient(engine, settings) as fetcher:

            def poll() -> int:
                return run_poll_cycle(engine, settings, fetcher)

            if args.once:
                try:
                    poll()
                except Exception as error:
                    logger.error(
                        "One-shot AoC poll cycle failed",
                        extra={"error_type": type(error).__name__},
                    )
                    return 1
            else:
                run_forever(poll)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
