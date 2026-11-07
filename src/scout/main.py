"""Primary entry point for the `scout` console script."""

from __future__ import annotations

from scout.cli.app import main

__all__ = ["main"]

if __name__ == "__main__":  # pragma: no cover
    main()
