"""Expose the locally installed DeepSeek Harness as an A2A agent."""

from __future__ import annotations

__all__ = ["main"]

__version__ = "0.1.0"


def main() -> int:
    """Console-script entry point (delegates to :mod:`dsh_a2a.main`)."""
    from .main import run

    return run()
