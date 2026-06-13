#!/usr/bin/env python3
"""Entry point for the autonomous daily profit bot."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure project root is on path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import settings
from src.engine.bot import ProfitBot
from src.utils.logging import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Autonomous daily profit demo trading bot")
    parser.add_argument(
        "mode",
        nargs="?",
        default="run",
        choices=["run", "once", "status"],
        help="run=autonomous loop, once=single cycle, status=print state",
    )
    args = parser.parse_args()

    logger = setup_logging(settings.log_level)
    bot = ProfitBot()

    if args.mode == "once":
        bot.run_once()
    elif args.mode == "status":
        import json
        print(json.dumps(bot.status(), indent=2))
    else:
        bot.run_forever()


if __name__ == "__main__":
    main()
