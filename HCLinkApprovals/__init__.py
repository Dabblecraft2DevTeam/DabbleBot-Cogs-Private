"""HCLinkApprovals cog package root."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from redbot.core.bot import Red

from .hclink_approvals import HCLinkApprovals

with open(Path(__file__).parent / "info.json") as fp:
    __red_end_user_data_statement__ = json.load(fp).get(
        "end_user_data_statement",
        "This cog does not persistently store any personal user data.",
    )


async def setup(bot: Red) -> None:
    """Standard Red cog setup hook."""
    cog = HCLinkApprovals(bot)
    await bot.add_cog(cog)
