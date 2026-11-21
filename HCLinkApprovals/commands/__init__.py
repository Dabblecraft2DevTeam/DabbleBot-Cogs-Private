"""Commands package exposing setup and cog class."""

from __future__ import annotations

from typing import Any

from redbot.core.bot import Red

from ..hclink_approvals import HCLinkApprovals


async def setup(bot_or_ctx: Any) -> None:
    """Setup entrypoint accepting Red bot instance or context."""
    bot = getattr(bot_or_ctx, "bot", bot_or_ctx)
    cog = HCLinkApprovals(bot)
    await bot.add_cog(cog)


__all__ = ["HCLinkApprovals", "setup"]
