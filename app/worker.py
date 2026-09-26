import argparse
import logging
import signal
import time

from app.config import get_settings
from app.db import init_db, session_scope
from app.services.runner import require_cloud_worker, tick
from app.sources.registry import default_registry

logger = logging.getLogger("leadlane.worker")
_stop = False


def _handle_stop(signum, _frame) -> None:
    global _stop
    logger.info("Signal %s received. The worker will stop after the current step.", signum)
    _stop = True


def run_forever(max_seconds: float = 0) -> None:
    settings = get_settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    init_db()
    deadline = time.monotonic() + max_seconds if max_seconds > 0 else None
    logger.info(
        "Worker started. Poll %.1fs. Each bot pauses at its source's free quota and resumes when that quota resets.",
        settings.worker_poll_seconds,
    )
    while not _stop:
        if deadline is not None and time.monotonic() >= deadline:
            logger.info("Reached --max-seconds. Exiting.")
            break
        try:
            with session_scope() as db:
                action = tick(db, settings, default_registry)
            logger.debug("Tick %s", action)
            if deadline is not None and action == "idle":
                logger.info("Nothing is due. Exiting this scheduled run.")
                break
        except Exception:
            logger.exception("Tick failed")
        slept = 0.0
        while slept < settings.worker_poll_seconds and not _stop:
            if deadline is not None and time.monotonic() >= deadline:
                break
            time.sleep(min(0.25, settings.worker_poll_seconds - slept))
            slept += 0.25


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Leadlane collection until stopped.")
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=0,
        help="Exit after this many seconds, or sooner when nothing is due. Used by GitHub Actions. 0 means run until signalled.",
    )
    args = parser.parse_args()
    require_cloud_worker()
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    run_forever(max_seconds=args.max_seconds)


if __name__ == "__main__":
    main()
