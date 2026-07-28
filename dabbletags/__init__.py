"""DabbleTags — Custom slash command tags powered by TagScriptEngine.

Replaces the broken slashtags cog with native discord.py app_commands tree
registration instead of raw HTTP calls.
"""

from redbot.core.bot import Red
from redbot.core.utils import get_end_user_data_statement_or_raise

from .dabbletags import DabbleTags

__red_end_user_data_statement__ = get_end_user_data_statement_or_raise(__file__)


async def setup(bot: Red) -> None:
    """Add the DabbleTags cog to the bot."""
    cog = DabbleTags(bot)
    await bot.add_cog(cog)