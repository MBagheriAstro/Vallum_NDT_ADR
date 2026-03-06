"""UI log buffer and logger for the webapp."""

import logging
from collections import deque

LOG_BUFFER: deque[str] = deque(maxlen=500)


class _UILogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        LOG_BUFFER.append(msg)


_handler = _UILogHandler()
_handler.setLevel(logging.INFO)
_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
)

logger = logging.getLogger("vallum.webapp")
logger.setLevel(logging.INFO)
logger.addHandler(_handler)
logger.propagate = False
