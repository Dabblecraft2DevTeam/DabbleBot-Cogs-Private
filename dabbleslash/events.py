from typing import Optional

import discord
from redbot.core import commands as red_commands
from redbot.core.bot import Red

from .commands import onetrueslash
from .utils import valid_app_name


async def before_hook(ctx: red_commands.Context):
    interaction: Optional[discord.Interaction] = getattr(ctx, "_interaction", None)
    if not interaction or getattr(ctx.command, "__commands_is_hybrid__", False):
        return
    ctx.interaction = interaction
    if not interaction.response.is_done():
        ctx._deferring = True  # type: ignore
        await interaction.response.defer(ephemeral=False)


async def on_user_update(before: discord.User, after: discord.User):
    bot: Red = after._state._get_client()  # type: ignore # DEP-WARN
    assert bot.user
    if after.id != bot.user.id:
        return
    if before.name == after.name:
        return
    # Command name is fixed to "db" — no need to update on bot name change
    # but we still want to notify owners if the bot name changed for awareness
    return
