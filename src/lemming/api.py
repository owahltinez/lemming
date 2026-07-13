"""Backward-compatible entry point re-exporting the FastAPI app."""

from .api.main import QuietPollFilter, app

__all__ = ["app", "QuietPollFilter"]
