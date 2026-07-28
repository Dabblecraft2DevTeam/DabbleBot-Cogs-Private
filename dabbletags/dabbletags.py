"""DabbleTags — main cog class.

Custom slash command tags powered by TagScriptEngine, registered via
discord.py's native app_commands tree (NOT raw HTTP).

Designed for Red-DiscordBot 3.5.24, discord.py 2.7.1, Python 3.11.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import discord
import TagScriptEngine as tse
from TagScriptEngine.adapter import IntAdapter, StringAdapter
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box, humanize_list, pagify

log = logging.getLogger("red.dabbletags")

# ── TagScriptEngine blocks ──────────────────────────────────────────────────

def _build_blocks() -> list:
    """Return the list of TSE blocks used by the interpreter."""
    return [
        tse.MathBlock(),
        tse.RandomBlock(),
        tse.RangeBlock(),
        tse.AnyBlock(),
        tse.IfBlock(),
        tse.AllBlock(),
        tse.BreakBlock(),
        tse.StrfBlock(),
        tse.StopBlock(),
        tse.AssignmentBlock(),
        tse.FiftyFiftyBlock(),
        tse.LooseVariableGetterBlock(),
        tse.SubstringBlock(),
        tse.EmbedBlock(),
        tse.ReplaceBlock(),
        tse.PythonBlock(),
        tse.RequireBlock(),
        tse.BlacklistBlock(),
        tse.URLEncodeBlock(),
        tse.CommandBlock(),
        tse.RedirectBlock(),
        tse.OverrideBlock(),
        tse.CooldownBlock(),
    ]

# ── Option type mapping ─────────────────────────────────────────────────────

OPTION_TYPE_MAP: Dict[str, Tuple[discord.AppCommandOptionType, type]] = {
    "string": (discord.AppCommandOptionType.string, str),
    "integer": (discord.AppCommandOptionType.integer, int),
    "boolean": (discord.AppCommandOptionType.boolean, bool),
    "user": (discord.AppCommandOptionType.user, discord.User),
    "channel": (discord.AppCommandOptionType.channel, discord.TextChannel),
    "role": (discord.AppCommandOptionType.role, discord.Role),
    "number": (discord.AppCommandOptionType.number, float),
}

# ── Custom adapters for TSE seed variables ──────────────────────────────────

class MemberAdapter(StringAdapter):
    """Wraps a discord Member/User so TSE can access its properties.

    Falls back to StringAdapter for basic string operations.
    """

    def __init__(self, member: discord.abc.User):
        self.member = member
        super().__init__(str(member))

    def get_value(self, ctx: tse.Verb) -> str:  # type: ignore[override]
        prop = ctx.parameter
        if prop is None:
            return f"<@{self.member.id}>"
        prop = prop.lower()
        if prop == "id":
            return str(self.member.id)
        elif prop == "name":
            return self.member.name
        elif prop == "nick" or prop == "nickname":
            return getattr(self.member, "nick", self.member.name)
        elif prop == "mention":
            return self.member.mention
        elif prop == "discriminator":
            return getattr(self.member, "discriminator", "0")
        elif prop == "avatar" or prop == "avatar_url":
            return str(self.member.display_avatar.url)
        elif prop == "created_at":
            return str(self.member.created_at)
        elif prop == "joined_at":
            return str(getattr(self.member, "joined_at", "N/A"))
        return super().get_value(ctx)


class ChannelAdapter(StringAdapter):
    """Wraps a discord channel for TSE seed variables."""

    def __init__(self, channel: discord.abc.GuildChannel):
        self.channel = channel
        super().__init__(str(channel))

    def get_value(self, ctx: tse.Verb) -> str:  # type: ignore[override]
        prop = ctx.parameter
        if prop is None:
            return self.channel.mention if hasattr(self.channel, "mention") else str(self.channel)
        prop = prop.lower()
        if prop == "id":
            return str(self.channel.id)
        elif prop == "name":
            return self.channel.name
        elif prop == "mention":
            return getattr(self.channel, "mention", str(self.channel))
        elif prop == "type":
            return str(getattr(self.channel, "type", "unknown"))
        return super().get_value(ctx)


class GuildAdapter(StringAdapter):
    """Wraps a discord Guild for TSE seed variables."""

    def __init__(self, guild: discord.Guild):
        self.guild = guild
        super().__init__(str(guild))

    def get_value(self, ctx: tse.Verb) -> str:  # type: ignore[override]
        prop = ctx.parameter
        if prop is None:
            return self.guild.name
        prop = prop.lower()
        if prop == "id":
            return str(self.guild.id)
        elif prop == "name":
            return self.guild.name
        elif prop == "member_count":
            return str(self.guild.member_count)
        elif prop == "icon":
            return str(self.guild.icon_url) if self.guild.icon else "None"
        return super().get_value(ctx)


class RoleAdapter(StringAdapter):
    """Wraps a discord Role for TSE seed variables."""

    def __init__(self, role: discord.Role):
        self.role = role
        super().__init__(str(role))

    def get_value(self, ctx: tse.Verb) -> str:  # type: ignore[override]
        prop = ctx.parameter
        if prop is None:
            return self.role.mention
        prop = prop.lower()
        if prop == "id":
            return str(self.role.id)
        elif prop == "name":
            return self.role.name
        elif prop == "mention":
            return self.role.mention
        elif prop == "color":
            return str(self.role.color)
        return super().get_value(ctx)


# ── Confirmation view ───────────────────────────────────────────────────────

class ConfirmView(discord.ui.View):
    """A simple button-based confirmation view for destructive actions."""

    def __init__(self, user: discord.abc.User, timeout: float = 30.0):
        super().__init__(timeout=timeout)
        self.user = user
        self.value: Optional[bool] = None

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa: ANN001
        if interaction.user != self.user:
            await interaction.response.send_message(
                "This confirmation is not for you.", ephemeral=True
            )
            return
        self.value = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa: ANN001
        if interaction.user != self.user:
            await interaction.response.send_message(
                "This confirmation is not for you.", ephemeral=True
            )
            return
        self.value = False
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


# ── Tag name validation ─────────────────────────────────────────────────────

_TAG_NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")


def _validate_tag_name(name: str) -> bool:
    """Return True if the name is a valid slash command name."""
    return bool(_TAG_NAME_RE.match(name.lower()))


# ── Dynamic slash command builder ───────────────────────────────────────────

def _make_tag_callback(cog: "DabbleTags", guild_id: int, tag_name: str):
    """Create a dynamic async callback for a tag's slash command.

    The callback is injected as the ``self`` parameter via a closure on
    *cog*, so it matches the signature app_commands expects:
    ``callback(interaction, **options)``.
    """

    async def _callback(interaction: discord.Interaction, **kwargs: Any) -> None:
        await cog._process_tag_interaction(interaction, tag_name, guild_id, kwargs)

    return _callback


def _build_command_parameters(
    options: List[Dict[str, Any]],
) -> Dict[str, app_commands.CommandParameter]:
    """Build a dict of CommandParameter objects from option dicts.

    We construct them directly rather than using type annotations because
    the options are dynamic (defined at runtime by the guild admin).
    """
    params: Dict[str, app_commands.CommandParameter] = {}
    for opt in options:
        opt_name = opt["name"]
        opt_type_str = opt.get("type", "string")
        opt_desc = opt.get("description", "No description provided")
        opt_required = opt.get("required", True)

        if opt_type_str not in OPTION_TYPE_MAP:
            continue

        app_type, _py_type = OPTION_TYPE_MAP[opt_type_str]

        param = app_commands.CommandParameter(
            name=opt_name,
            description=opt_desc,
            required=opt_required,
            default=None if opt_required else None,
            type=app_type,
        )
        params[opt_name] = param

    return params


def _create_tag_command(
    cog: "DabbleTags",
    guild_id: int,
    tag_name: str,
    tag_description: str,
    options: List[Dict[str, Any]],
) -> app_commands.Command:
    """Create an ``app_commands.Command`` for a tag.

    The callback's ``__signature__`` is patched *before* the Command is
    instantiated so discord.py's ``_extract_parameters_from_callback`` sees
    the right parameters.
    """
    import inspect

    callback = _make_tag_callback(cog, guild_id, tag_name)

    # Build the fake signature
    sig_params: List[inspect.Parameter] = [
        inspect.Parameter(
            "interaction",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=discord.Interaction,
        )
    ]
    for opt in options:
        opt_name = opt["name"]
        opt_type_str = opt.get("type", "string")
        opt_required = opt.get("required", True)

        if opt_type_str not in OPTION_TYPE_MAP:
            continue

        _, py_type = OPTION_TYPE_MAP[opt_type_str]
        default = inspect.Parameter.empty if opt_required else None
        sig_params.append(
            inspect.Parameter(
                opt_name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=py_type,
                default=default,
            )
        )

    callback.__signature__ = inspect.Signature(sig_params)  # type: ignore[attr-defined]
    callback.__discord_app_commands_guild_only__ = True  # type: ignore[attr-defined]

    cmd = app_commands.Command(
        name=tag_name,
        description=(tag_description or "A custom tag.")[:100],
        callback=callback,
    )
    return cmd


# ── Main cog ────────────────────────────────────────────────────────────────

class DabbleTags(commands.Cog):
    """Custom slash command tags with TagScriptEngine."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=20240728, force_registration=True)

        self.config.register_guild(
            tags={},  # {tag_name: {name, description, tagscript, author_id, uses, options}}
        )

        # Track which guilds we've registered commands for (for cleanup)
        self._registered_guilds: set[int] = set()

        # TagScriptEngine interpreter
        self._interpreter = tse.Interpreter(_build_blocks())

        # Schedule re-registration of existing tags after bot is ready
        self._init_task: Optional[asyncio.Task] = None
        self._init_task = asyncio.create_task(self._post_init())

    # ── Lifecycle ───────────────────────────────────────────────────────────

    async def _post_init(self) -> None:
        """Wait for bot ready, then re-register all tags from Config."""
        await self.bot.wait_until_red_ready()
        try:
            await self._register_all_tags()
        except Exception:
            log.exception("Failed to re-register tags during cog load")

    async def _register_all_tags(self) -> None:
        """Re-register all existing tags from Config into the command tree."""
        all_guilds = await self.config.all_guilds()
        for guild_id_str, guild_data in all_guilds.items():
            guild_id = int(guild_id_str)
            tags: Dict[str, Any] = guild_data.get("tags", {})
            for tag_name, tag_data in tags.items():
                try:
                    await self._register_tag_command(guild_id, tag_name, tag_data)
                except app_commands.CommandAlreadyRegistered:
                    log.debug("Tag %s already registered in guild %s", tag_name, guild_id)
                except Exception:
                    log.exception(
                        "Failed to re-register tag %s in guild %s", tag_name, guild_id
                    )

    async def cog_unload(self) -> None:
        """Remove all registered tag commands from the tree on unload."""
        if self._init_task and not self._init_task.done():
            self._init_task.cancel()

        for guild_id in list(self._registered_guilds):
            guild_obj = discord.Object(id=guild_id)
            # We need to know which commands to remove. We'll iterate over
            # all guild commands and remove the ones that belong to us.
            # Since we can't easily track individual command names, we'll
            # re-read config.
            try:
                guild_data = await self.config.guild(discord.Object(id=guild_id)).tags()
                for tag_name in guild_data:
                    try:
                        self.bot.tree.remove_command(tag_name, guild=guild_obj)
                    except Exception:
                        pass
                # Sync to clear them from Discord
                try:
                    await self.bot.tree.sync(guild=guild_obj)
                except Exception:
                    log.debug("Failed to sync guild %s during unload", guild_id)
            except Exception:
                log.exception("Error during cleanup for guild %s", guild_id)

    # ── Tag command registration ────────────────────────────────────────────

    async def _register_tag_command(
        self, guild_id: int, tag_name: str, tag_data: Dict[str, Any]
    ) -> app_commands.Command:
        """Register a single tag as a guild slash command."""
        guild_obj = discord.Object(id=guild_id)

        # Remove existing command with the same name if present
        try:
            self.bot.tree.remove_command(tag_name, guild=guild_obj)
        except Exception:
            pass

        options = tag_data.get("options", [])
        description = tag_data.get("description", "A custom tag.")

        cmd = _create_tag_command(
            cog=self,
            guild_id=guild_id,
            tag_name=tag_name,
            tag_description=description,
            options=options,
        )

        self.bot.tree.add_command(cmd, guild=guild_obj)
        self._registered_guilds.add(guild_id)
        return cmd

    async def _unregister_tag_command(self, guild_id: int, tag_name: str) -> None:
        """Remove a tag's slash command from the tree."""
        guild_obj = discord.Object(id=guild_id)
        try:
            self.bot.tree.remove_command(tag_name, guild=guild_obj)
        except Exception:
            log.debug("Could not remove command %s from guild %s", tag_name, guild_id)

    async def _sync_guild(self, guild_id: int) -> None:
        """Sync the command tree for a specific guild."""
        guild_obj = discord.Object(id=guild_id)
        try:
            await self.bot.tree.sync(guild=guild_obj)
        except discord.Forbidden:
            log.warning(
                "Missing applications.commands scope in guild %s — "
                "slash commands will not appear until the bot is re-invited "
                "with the scope.",
                guild_id,
            )
        except Exception:
            log.exception("Failed to sync command tree for guild %s", guild_id)

    # ── TagScript processing ────────────────────────────────────────────────

    def _build_seed_variables(
        self,
        interaction: discord.Interaction,
        options: List[Dict[str, Any]],
        kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build TSE seed variables from the interaction and option values."""
        seed_vars: Dict[str, Any] = {}

        # Author
        if interaction.user:
            seed_vars["author"] = MemberAdapter(interaction.user)

        # Channel
        if interaction.channel:
            seed_vars["channel"] = ChannelAdapter(interaction.channel)

        # Server / Guild
        if interaction.guild:
            seed_vars["server"] = GuildAdapter(interaction.guild)

        # Options — map each option value to the appropriate TSE adapter
        for opt in options:
            opt_name = opt["name"]
            opt_type = opt.get("type", "string")
            value = kwargs.get(opt_name)

            if value is None:
                seed_vars[opt_name] = StringAdapter("")
                continue

            if opt_type == "string":
                seed_vars[opt_name] = StringAdapter(str(value))
            elif opt_type == "integer":
                seed_vars[opt_name] = IntAdapter(int(value))
            elif opt_type == "number":
                seed_vars[opt_name] = StringAdapter(str(value))
            elif opt_type == "boolean":
                seed_vars[opt_name] = StringAdapter(str(value).lower())
            elif opt_type == "user":
                seed_vars[opt_name] = MemberAdapter(value)
            elif opt_type == "channel":
                seed_vars[opt_name] = ChannelAdapter(value)
            elif opt_type == "role":
                seed_vars[opt_name] = RoleAdapter(value)
            else:
                seed_vars[opt_name] = StringAdapter(str(value))

        return seed_vars

    async def _process_tag_interaction(
        self,
        interaction: discord.Interaction,
        tag_name: str,
        guild_id: int,
        kwargs: Dict[str, Any],
    ) -> None:
        """Handle a slash command invocation for a tag."""
        # Look up the tag from Config
        guild_obj = discord.Object(id=guild_id)
        tags: Dict[str, Any] = await self.config.guild(guild_obj).tags()

        tag_data = tags.get(tag_name)
        if tag_data is None:
            await interaction.response.send_message(
                "This tag no longer exists.", ephemeral=True
            )
            return

        tagscript: str = tag_data.get("tagscript", "")
        options: List[Dict[str, Any]] = tag_data.get("options", [])

        # Build seed variables
        seed_vars = self._build_seed_variables(interaction, options, kwargs)

        # Process through TagScriptEngine
        try:
            output = self._interpreter.process(tagscript, seed_variables=seed_vars)
        except tse.WorkloadExceededError:
            await interaction.response.send_message(
                "Tag processing exceeded the workload limit.", ephemeral=True
            )
            return
        except Exception:
            log.exception("Error processing tag %s", tag_name)
            await interaction.response.send_message(
                "An error occurred while processing this tag.", ephemeral=True
            )
            return

        # Increment use count
        tag_data["uses"] = tag_data.get("uses", 0) + 1
        tags[tag_name] = tag_data
        await self.config.guild(guild_obj).tags.set(tags)

        # Determine response content
        body = output.body or ""
        actions = output.actions
        embed = actions.get("embed", discord.utils.MISSING)
        ephemeral = actions.get("hide", False)

        # Truncate body if needed
        if len(body) > 2000:
            body = body[:1997] + "..."

        # Send response
        if not body and embed is discord.utils.MISSING:
            body = "\u200b"  # zero-width space for empty messages with embeds

        if embed is not discord.utils.MISSING and embed is not None:
            await interaction.response.send_message(
                content=body if body else None,
                embed=embed,
                ephemeral=ephemeral,
            )
        else:
            if not body:
                body = "Tag returned no output."
            await interaction.response.send_message(
                content=body,
                ephemeral=ephemeral,
            )

    # ── Prefix commands (dtag) ──────────────────────────────────────────────

    @commands.group(name="dtag", invoke_without_command=True)
    async def dtag(self, ctx: commands.Context) -> None:
        """Manage custom slash command tags."""
        await ctx.send_help(ctx.command)

    @dtag.command(name="create")
    @commands.mod_or_permissions(manage_guild=True)
    async def dtag_create(self, ctx: commands.Context, name: str, *, tagscript: str) -> None:
        """Create a guild tag that registers as a slash command.

        Tag names must be lowercase, 1-32 characters, and contain only
        letters, numbers, and underscores.

        The tagscript is processed through TagScriptEngine when the slash
        command is invoked. Use blocks like {if}, {math}, {random}, etc.
        """
        if ctx.guild is None:
            await ctx.send("Tags can only be created in servers.")
            return

        name = name.lower()
        if not _validate_tag_name(name):
            await ctx.send(
                "Tag names must be 1-32 characters, lowercase, and contain "
                "only letters, numbers, and underscores."
            )
            return

        tags: Dict[str, Any] = await self.config.guild(ctx.guild).tags()

        if name in tags:
            await ctx.send(f"A tag named `{name}` already exists. Use `{ctx.clean_prefix}dtag edit` to modify it.")
            return

        tag_data = {
            "name": name,
            "description": f"Custom tag: {name}",
            "tagscript": tagscript,
            "author_id": ctx.author.id,
            "uses": 0,
            "options": [],
        }

        tags[name] = tag_data
        await self.config.guild(ctx.guild).tags.set(tags)

        # Register the slash command
        try:
            await self._register_tag_command(ctx.guild.id, name, tag_data)
            await self._sync_guild(ctx.guild.id)
        except app_commands.CommandAlreadyRegistered:
            await ctx.send(
                f"Tag `{name}` was stored but a slash command with that name "
                "is already registered (possibly by another cog)."
            )
            return
        except Exception:
            log.exception("Failed to register tag command %s", name)
            # Roll back the config save
            tags.pop(name, None)
            await self.config.guild(ctx.guild).tags.set(tags)
            await ctx.send(f"Failed to register the slash command for `{name}`. The tag was not created.")
            return

        await ctx.send(
            f"✅ Tag `{name}` created and registered as a slash command.\n"
            f"Use `/{name}` to invoke it.\n"
            f"Use `{ctx.clean_prefix}dtag addoption {name} <option>` to add options."
        )

    @dtag.command(name="delete")
    @commands.mod_or_permissions(manage_guild=True)
    async def dtag_delete(self, ctx: commands.Context, name: str) -> None:
        """Delete a guild tag and unregister its slash command."""
        if ctx.guild is None:
            await ctx.send("Tags can only be deleted in servers.")
            return

        name = name.lower()
        tags: Dict[str, Any] = await self.config.guild(ctx.guild).tags()

        if name not in tags:
            await ctx.send(f"No tag named `{name}` exists.")
            return

        # Confirmation view
        view = ConfirmView(ctx.author)
        msg = await ctx.send(
            f"Are you sure you want to delete the tag `{name}`? "
            f"It has been used **{tags[name].get('uses', 0)}** times.",
            view=view,
        )
        await view.wait()

        if view.value is not True:
            await msg.edit(content="Tag deletion cancelled.", view=None)
            return

        del tags[name]
        await self.config.guild(ctx.guild).tags.set(tags)

        await self._unregister_tag_command(ctx.guild.id, name)
        await self._sync_guild(ctx.guild.id)

        await msg.edit(content=f"✅ Tag `{name}` deleted and slash command removed.", view=None)

    @dtag.command(name="edit")
    @commands.mod_or_permissions(manage_guild=True)
    async def dtag_edit(self, ctx: commands.Context, name: str, *, tagscript: str) -> None:
        """Edit a tag's tagscript."""
        if ctx.guild is None:
            await ctx.send("Tags can only be edited in servers.")
            return

        name = name.lower()
        tags: Dict[str, Any] = await self.config.guild(ctx.guild).tags()

        if name not in tags:
            await ctx.send(f"No tag named `{name}` exists.")
            return

        tags[name]["tagscript"] = tagscript
        await self.config.guild(ctx.guild).tags.set(tags)

        # Re-register to update the command (description may have changed)
        try:
            await self._register_tag_command(ctx.guild.id, name, tags[name])
            await self._sync_guild(ctx.guild.id)
        except Exception:
            log.exception("Failed to re-register tag command %s", name)
            await ctx.send(f"Tag script updated, but failed to sync the slash command for `{name}`.")
            return

        await ctx.send(f"✅ Tag `{name}` updated and slash command synced.")

    @dtag.command(name="list")
    async def dtag_list(self, ctx: commands.Context) -> None:
        """List all guild tags."""
        if ctx.guild is None:
            await ctx.send("Tags can only be listed in servers.")
            return

        tags: Dict[str, Any] = await self.config.guild(ctx.guild).tags()

        if not tags:
            await ctx.send("No tags have been created in this server yet.")
            return

        tag_list = sorted(tags.keys())
        lines = [f"**{name}** — uses: {tags[name].get('uses', 0)}" for name in tag_list]

        for page in pagify("\n".join(lines)):
            await ctx.send(box(page, lang="md"))

    @dtag.command(name="info")
    async def dtag_info(self, ctx: commands.Context, name: str) -> None:
        """Show information about a tag."""
        if ctx.guild is None:
            await ctx.send("Tags can only be viewed in servers.")
            return

        name = name.lower()
        tags: Dict[str, Any] = await self.config.guild(ctx.guild).tags()

        if name not in tags:
            await ctx.send(f"No tag named `{name}` exists.")
            return

        tag = tags[name]
        author_id = tag.get("author_id", 0)
        uses = tag.get("uses", 0)
        tagscript = tag.get("tagscript", "")
        description = tag.get("description", "No description")
        options = tag.get("options", [])

        embed = discord.Embed(
            title=f"Tag: {name}",
            description=description,
            color=await ctx.embed_color(),
        )
        embed.add_field(name="Author", value=f"<@{author_id}> ({author_id})", inline=True)
        embed.add_field(name="Uses", value=str(uses), inline=True)
        embed.add_field(name="Script Length", value=f"{len(tagscript)} chars", inline=True)

        if options:
            opt_lines = [f"• `{o['name']}` ({o.get('type', 'string')}): {o.get('description', 'N/A')}" for o in options]
            embed.add_field(name="Options", value="\n".join(opt_lines), inline=False)
        else:
            embed.add_field(name="Options", value="None", inline=False)

        await ctx.send(embed=embed)

    @dtag.command(name="raw")
    async def dtag_raw(self, ctx: commands.Context, name: str) -> None:
        """Show the raw tagscript of a tag."""
        if ctx.guild is None:
            await ctx.send("Tags can only be viewed in servers.")
            return

        name = name.lower()
        tags: Dict[str, Any] = await self.config.guild(ctx.guild).tags()

        if name not in tags:
            await ctx.send(f"No tag named `{name}` exists.")
            return

        tagscript = tags[name].get("tagscript", "")
        for page in pagify(tagscript):
            await ctx.send(box(page))

    @dtag.command(name="addoption")
    @commands.mod_or_permissions(manage_guild=True)
    async def dtag_addoption(
        self, ctx: commands.Context, name: str, *, option_spec: str
    ) -> None:
        """Add an option to a tag's slash command.

        The option spec must be in the format:
        `option_name:type:description`

        Valid types: string, integer, boolean, user, channel, role, number

        Example:
        `dtag addoption mytag target:user:The user to target`
        """
        if ctx.guild is None:
            await ctx.send("Tags can only be modified in servers.")
            return

        name = name.lower()
        tags: Dict[str, Any] = await self.config.guild(ctx.guild).tags()

        if name not in tags:
            await ctx.send(f"No tag named `{name}` exists.")
            return

        # Parse option spec: name:type:description
        parts = option_spec.split(":", 2)
        if len(parts) < 2:
            await ctx.send(
                "Invalid option spec. Use `option_name:type:description`\n"
                "Valid types: string, integer, boolean, user, channel, role, number"
            )
            return

        opt_name = parts[0].strip().lower()
        opt_type = parts[1].strip().lower()
        opt_desc = parts[2].strip() if len(parts) > 2 else "No description provided"

        if not _validate_tag_name(opt_name):
            await ctx.send(
                "Option names must be 1-32 characters, lowercase, and contain "
                "only letters, numbers, and underscores."
            )
            return

        if opt_type not in OPTION_TYPE_MAP:
            valid_types = humanize_list(list(OPTION_TYPE_MAP.keys()))
            await ctx.send(
                f"Invalid option type `{opt_type}`. Valid types: {valid_types}"
            )
            return

        # Check for duplicate option names
        existing_options: List[Dict[str, Any]] = tags[name].get("options", [])
        for existing in existing_options:
            if existing["name"] == opt_name:
                await ctx.send(f"Option `{opt_name}` already exists on tag `{name}`.")
                return

        option_dict = {
            "name": opt_name,
            "type": opt_type,
            "description": opt_desc[:100],
            "required": True,
        }

        existing_options.append(option_dict)
        tags[name]["options"] = existing_options
        await self.config.guild(ctx.guild).tags.set(tags)

        # Re-register the command with the new option
        try:
            await self._register_tag_command(ctx.guild.id, name, tags[name])
            await self._sync_guild(ctx.guild.id)
        except Exception:
            log.exception("Failed to re-register tag command %s with new option", name)
            await ctx.send(f"Option added to config, but failed to sync slash command for `{name}`.")
            return

        await ctx.send(
            f"✅ Option `{opt_name}` ({opt_type}) added to tag `{name}`.\n"
            f"The value will be available as `{{{opt_name}}}` in the tagscript."
        )

    @dtag.command(name="removeoption")
    @commands.mod_or_permissions(manage_guild=True)
    async def dtag_removeoption(
        self, ctx: commands.Context, name: str, option_name: str
    ) -> None:
        """Remove an option from a tag's slash command."""
        if ctx.guild is None:
            await ctx.send("Tags can only be modified in servers.")
            return

        name = name.lower()
        option_name = option_name.lower()
        tags: Dict[str, Any] = await self.config.guild(ctx.guild).tags()

        if name not in tags:
            await ctx.send(f"No tag named `{name}` exists.")
            return

        existing_options: List[Dict[str, Any]] = tags[name].get("options", [])
        new_options = [o for o in existing_options if o["name"] != option_name]

        if len(new_options) == len(existing_options):
            await ctx.send(f"Option `{option_name}` does not exist on tag `{name}`.")
            return

        tags[name]["options"] = new_options
        await self.config.guild(ctx.guild).tags.set(tags)

        try:
            await self._register_tag_command(ctx.guild.id, name, tags[name])
            await self._sync_guild(ctx.guild.id)
        except Exception:
            log.exception("Failed to re-register tag command %s after option removal", name)
            await ctx.send(f"Option removed from config, but failed to sync slash command for `{name}`.")
            return

        await ctx.send(f"✅ Option `{option_name}` removed from tag `{name}`.")

    # ── Data privacy ────────────────────────────────────────────────────────

    async def red_delete_data_for_user(
        self,
        *,
        requester: "Red",
        user_id: int,
        guild_id: Optional[int] = None,
    ) -> None:
        """Delete all data stored for a user.

        We delete tags created by the user (author_id match) and clear
        their author_id from any tags we don't delete.
        """
        if guild_id is not None:
            guild_obj = discord.Object(id=guild_id)
            tags: Dict[str, Any] = await self.config.guild(guild_obj).tags()
            to_delete: List[str] = []
            for tag_name, tag_data in tags.items():
                if tag_data.get("author_id") == user_id:
                    to_delete.append(tag_name)

            for tag_name in to_delete:
                del tags[tag_name]
                await self._unregister_tag_command(guild_id, tag_name)

            if to_delete:
                await self.config.guild(guild_obj).tags.set(tags)
                await self._sync_guild(guild_id)
        else:
            # All guilds
            all_guilds = await self.config.all_guilds()
            for guild_id_str, guild_data in all_guilds.items():
                gid = int(guild_id_str)
                guild_obj = discord.Object(id=gid)
                tags = guild_data.get("tags", {})
                to_delete = [
                    name for name, data in tags.items()
                    if data.get("author_id") == user_id
                ]
                for tag_name in to_delete:
                    del tags[tag_name]
                    await self._unregister_tag_command(gid, tag_name)
                if to_delete:
                    await self.config.guild(guild_obj).tags.set(tags)
                    await self._sync_guild(gid)

    async def red_get_data_for_user(
        self, *, user_id: int, guild_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Retrieve all data stored for a user."""
        result: Dict[str, Any] = {"tags_created": []}

        if guild_id is not None:
            guild_obj = discord.Object(id=guild_id)
            tags: Dict[str, Any] = await self.config.guild(guild_obj).tags()
            for tag_name, tag_data in tags.items():
                if tag_data.get("author_id") == user_id:
                    result["tags_created"].append({
                        "guild_id": guild_id,
                        "tag_name": tag_name,
                        "uses": tag_data.get("uses", 0),
                    })
        else:
            all_guilds = await self.config.all_guilds()
            for guild_id_str, guild_data in all_guilds.items():
                gid = int(guild_id_str)
                tags = guild_data.get("tags", {})
                for tag_name, tag_data in tags.items():
                    if tag_data.get("author_id") == user_id:
                        result["tags_created"].append({
                            "guild_id": gid,
                            "tag_name": tag_name,
                            "uses": tag_data.get("uses", 0),
                        })

        return result