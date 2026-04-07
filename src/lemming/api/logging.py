import logging

# Paths that should not appear in the uvicorn access log (e.g. polling endpoints).
QUIET_PATHS = {"/api/data", "GET /api/tasks/", "/api/files"}


class QuietPollFilter(logging.Filter):
    """Suppress uvicorn access-log lines for high-frequency polling endpoints."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(path in msg for path in QUIET_PATHS)
