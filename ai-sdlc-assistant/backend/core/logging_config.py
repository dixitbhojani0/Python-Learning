"""
backend/core/logging_config.py

Configures the root logger once, at app startup, before anything else can log.

Every module in backend/ does `logger = logging.getLogger(__name__)` and relies on
propagation to the root logger. Without this, the root logger has no handler and
Python's logging.lastResort kicks in — which only prints WARNING and above, silently
dropping every logger.info() call (startup messages, corrective RAG traces, etc.)
and never writing anything to disk.
"""
import logging
import time
from datetime import date
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from backend.core.settings import settings

_LOGS_DIR = Path(__file__).parent.parent.parent / "logs"


class _DailyFileHandler(TimedRotatingFileHandler):
    """
    Writes straight to logs/app-YYYY-MM-DD.log — today's file already carries
    today's date, it doesn't wait until midnight rollover to be renamed into one.
    At midnight it just points the stream at tomorrow's dated file.
    """

    def __init__(self, backup_days: int, encoding: str = "utf-8") -> None:
        self._backup_days = backup_days
        super().__init__(str(self._path_for(date.today())), when="midnight", encoding=encoding)

    @staticmethod
    def _path_for(day: date) -> Path:
        return _LOGS_DIR / f"app-{day:%Y-%m-%d}.log"

    def doRollover(self) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None
        self.baseFilename = str(self._path_for(date.today()))
        self.stream = self._open()
        self.rolloverAt = self.computeRollover(int(time.time()))
        self._delete_old_logs()

    def _delete_old_logs(self) -> None:
        for old_file in sorted(_LOGS_DIR.glob("app-*.log"))[:-self._backup_days]:
            old_file.unlink(missing_ok=True)


def setup_logging() -> None:
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = _DailyFileHandler(backup_days=14)   # keep 14 days, auto-delete older
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logging.basicConfig(level=settings.LOG_LEVEL, handlers=[file_handler, console_handler])
