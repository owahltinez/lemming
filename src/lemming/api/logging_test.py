import logging
from lemming import api


def test_quiet_poll_filter():
    """QuietPollFilter suppresses access-log lines for polling endpoints."""
    filt = api.QuietPollFilter()

    # Simulate a uvicorn access-log record for the polling endpoints.
    for path in ("/api/data",):
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg='%s - "%s %s HTTP/%s" %d',
            args=("127.0.0.1:55964", "GET", path, "1.1", 200),
            exc_info=None,
        )
        assert filt.filter(record) is False

    # GET /api/tasks/{task_id} and GET /api/tasks/{task_id}/log should be quieted.
    for path in ("/api/tasks/abc-123", "/api/tasks/xyz-789/log"):
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg='%s - "%s %s HTTP/%s" %d',
            args=("127.0.0.1:55964", "GET", path, "1.1", 200),
            exc_info=None,
        )
        assert filt.filter(record) is False

    # Non-polling endpoints should still be logged.
    record_other = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:55964", "GET", "/api/runners", "1.1", 200),
        exc_info=None,
    )
    assert filt.filter(record_other) is True

    # Important: POST /api/tasks should NOT be quieted.
    record_post = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:55964", "POST", "/api/tasks/abc-123", "1.1", 200),
        exc_info=None,
    )
    assert filt.filter(record_post) is True
