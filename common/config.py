"""Project-wide paths and constants."""

from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

# Downloaded market data lives here (git-ignored). Override with TRADING_DATA_DIR.
DATA_DIR = Path(os.environ.get("TRADING_DATA_DIR", ROOT_DIR / "data"))
