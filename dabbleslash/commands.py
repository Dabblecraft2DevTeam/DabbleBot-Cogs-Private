import asyncio
import functools
import heapq
import operator
from copy import copy
from typing import Awaitable, Callable, Dict, List, Optional, Tuple, cast

import discord
from rapidfuzz import fuzz
from redbot.core import app_commands, commands
from redbot.core.bot import Red
from redbot.core.commands.help import HelpSettings
from redbot.core.i18n import set_contextual_locale

from .context import InterContext
from .utils import walk_aliases


@app_commands.command(extras={"red_force_enable": True})
async def onetrueslash(
    interaction: discord.Interaction,
    command: str,
    arguments: Optional[str] = None,
    attachment: Optional[discord.Attachment] = None,
) -> None:
    """
    The one true slash command.

    Parameters
    -----------
    command: str
        The text-based command to run.
    arguments: Optional[str]
        The arguments to provide to the command, if any.
    attachment: Optional[Attachment]
        The attached file to provide to the command, if any.
    """
    assert isinstance(interaction.client, Red)
    set_contextual_locale(str(interaction.guild_locale or interaction.locale))
    actual = interaction.client.get_command(command)
    ctx = await InterContext.from_interaction(interaction, recreate_message=True)
    error = None
    if command == "help":
        ctx._deferring = True
        # Moving ctx._interaction can cause check errors with some hybrid commands
        # see https://github.com/Zephyrkul/FluffyCogs/issues/75 for details
        # ctx.interaction = interaction
        await interaction.response.defer(ephemeral=True)
        actual = None
        cog = None
        if arguments:
            actual = interaction.client.get_command(arguments)
            if actual and (signature := actual.signature):
                actual = copy(actual)
                actual.usage = f"arguments:{signature}"
            if actual is None:
                # Check if the argument is a cog name
                cog = interaction.client.get_cog(arguments)
        await interaction.client.send_help_for(
            ctx, actual or cog or interaction.client, from_help_command=True
        )
    else:
        ferror: asyncio.Task[Tuple[InterContext, commands.CommandError]] = asyncio.create_task(
            interaction.client.wait_for("command_error", check=lambda c, _: c is ctx)
        )
        ferror.add_done_callback(lambda _: setattr(ctx, "interaction", interaction))
        await interaction.client.invoke(ctx)
        if not interaction.response.is_done():
            ctx._deferring = True
            await interaction.response.defer(ephemeral=True)
        if ferror.done():
            error = ferror.exception() or ferror.result()[1]
        ferror.cancel()
    if ctx._deferring and not interaction.is_expired():
        if error is None:
            if ctx._ticked:
                await interaction.followup.send(ctx._ticked, ephemeral=True)
            else:
                await interaction.delete_original_response()
        elif isinstance(error, commands.CommandNotFound):
            await interaction.followup.send(
                f"❌ Command `{command}` was not found.", ephemeral=True
            )
        elif isinstance(error, commands.CheckFailure):
            await interaction.followup.send(
                f"❌ You don't have permission to run `{command}`.", ephemeral=True
            )


@onetrueslash.autocomplete("command")
async def onetrueslash_command_autocomplete(
    interaction: discord.Interaction, current: str
) -> List[app_commands.Choice[str]]:
    assert isinstance(interaction.client, Red)

    if not await interaction.client.allowed_by_whitelist_blacklist(interaction.user):
        return []

    ctx = await InterContext.from_interaction(interaction)
    if not await interaction.client.message_eligible_as_command(ctx.message):
        return []

    help_settings = await HelpSettings.from_context(ctx)
    if current:
        extracted = cast(
            List[str],
            await asyncio.get_event_loop().run_in_executor(
                None,
                heapq.nlargest,
                6,
                walk_aliases(interaction.client, show_hidden=help_settings.show_hidden),
                functools.partial(fuzz.token_sort_ratio, current),
            ),
        )
        extracted.append("help")
    else:
        extracted = ["help"]
    _filter: Callable[[commands.Command], Awaitable[bool]] = operator.methodcaller(
        "can_run" if help_settings.show_hidden else "can_see", ctx
    )
    matches: Dict[commands.Command, str] = {}
    for name in extracted:
        command = interaction.client.get_command(name)
        if not command or command in matches:
            continue
        try:
            if name == "help" and await command.can_run(ctx) or await _filter(command):
                if len(name) > 100:
                    name = name[:99] + "\N{HORIZONTAL ELLIPSIS}"
                matches[command] = name
        except commands.CommandError:
            pass
    # Include command short description in the choice name for better UX
    choices = []
    for command, name in matches.items():
        # Use the command's brief or docstring as the description
        brief = command.brief or ""
        if not brief and command.help:
            brief = command.help.split("\n")[0][:50]
        if brief:
            # Discord limits choice name to 100 chars
            display = f"{name} — {brief[:100 - len(name) - 3]}"
        else:
            display = name
        choices.append(app_commands.Choice(name=display, value=name))
    return choices


@onetrueslash.autocomplete("arguments")
async def onetrueslash_arguments_autocomplete(
    interaction: discord.Interaction, current: str
) -> List[app_commands.Choice[str]]:
    """Provide argument hints based on the selected command's signature."""
    assert isinstance(interaction.client, Red)

    if not await interaction.client.allowed_by_whitelist_blacklist(interaction.user):
        return []

    # Get the command the user selected in the command field
    command_name = None
    for option in interaction.data.get("options", []):
        if option["name"] == "command":
            command_name = option.get("value")
            break

    if not command_name:
        return []

    command = interaction.client.get_command(command_name)
    if not command:
        return []

    # Build hint choices from the command's signature/parameters
    signature = command.signature
    if not signature:
        return []

    # Parse the signature into individual argument hints
    # Signature looks like: "<user: Member> <reason: str>"
    hints = []
    params = command.clean_params
    for param_name, param in params.items():
        if param_name in ("ctx", "context"):
            continue
        # Build a hint string for this parameter
        param_type = param.annotation
        type_name = getattr(param_type, "__name__", str(param_type))
        required = param.default == param.empty
        if required:
            hint = f"📋 {param_name} ({type_name}) — required"
        else:
            hint = f"📋 {param_name} ({type_name}) — optional"
        # Use empty string as value so clicking the hint doesn't insert broken text
        hints.append(app_commands.Choice(name=hint[:100], value=""))

    # Also offer a combined hint showing the full signature (informational only)
    if signature and len(signature) <= 90:
        hints.insert(0, app_commands.Choice(
            name=f"ℹ️ Signature: {signature}",
            value="",
        ))

    # Filter by current input if the user has started typing
    if current:
        hints = [h for h in hints if current.lower() in h.name.lower()][:6]
    else:
        hints = hints[:6]

    return hints


@onetrueslash.error
async def onetrueslash_error(interaction: discord.Interaction, error: Exception):
    assert isinstance(interaction.client, Red)
    if isinstance(error, app_commands.CommandInvokeError):
        error = error.original
    error = getattr(error, "original", error)
    await interaction.client.on_command_error(
        await InterContext.from_interaction(interaction, recreate_message=True),
        commands.CommandInvokeError(error),
    )
