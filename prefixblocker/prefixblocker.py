import discord
from redbot.core import Config, commands

class PrefixBlocker(commands.Cog):
    """Blocks prefix commands and directs users to slash commands."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=847382947238, force_registration=True)
        
        default_guild = {
            "enabled": True
        }
        self.config.register_guild(**default_guild)
        
        # Register the before_invoke hook
        self._before_invoke = bot.before_invoke(self._block_prefix)
    
    async def red_delete_data_for_user(self, *, requester: str, user_id: int):
        """This cog does not store any user data."""
        return

    async def red_get_data_for_user(self, *, user_id: int):
        """This cog does not store any user data."""
        return {}
    
    def cog_unload(self):
        # Remove the before_invoke hook when cog is unloaded
        self.bot.remove_before_invoke_hook(self._before_invoke)
    
    async def _block_prefix(self, ctx: commands.Context):
        """Intercept prefix commands before they execute."""
        # Skip if not in a guild
        if not ctx.guild:
            return
        
        # Skip if author is a bot owner
        if await self.bot.is_owner(ctx.author):
            return
        
        # Skip if blocker is disabled for this guild
        enabled = await self.config.guild(ctx.guild).enabled()
        if not enabled:
            return
        
        # Skip if this was invoked via slash command or mention
        # DabbleSlash sets _interaction on the context before invocation
        if getattr(ctx, "_interaction", None) is not None:
            return
        
        # Check if the interaction was set by DabbleSlash's before_hook
        if getattr(ctx, "interaction", None) is not None:
            return
        
        # Check if the message content starts with a mention prefix
        content = ctx.message.content
        bot_id = self.bot.user.id
        if content.startswith(f"<@{bot_id}>") or content.startswith(f"<@!{bot_id}>"):
            return
        
        # Check if the prefix is a mention (DabbleSlash uses mention-like prefixes)
        prefix = ctx.prefix or ""
        if prefix.startswith("<@"):
            return
        
        # This is a prefix command — block it
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass
        
        try:
            await ctx.channel.send(
                f"{ctx.author.mention}, please use slash commands (like `/db`) or mention the bot (`@DabbleBot`) instead of using prefix commands.",
                delete_after=10
            )
        except (discord.Forbidden, discord.HTTPException):
            pass
        
        # Cancel the command execution by raising CheckFailure
        raise commands.CheckFailure("Prefix commands are disabled in this server.")

    @commands.group(name="prefixblock")
    @commands.is_owner()
    async def _prefixblock(self, ctx: commands.Context):
        """Manage PrefixBlocker settings."""
        pass

    @_prefixblock.command(name="toggle")
    async def _toggle(self, ctx: commands.Context):
        """Toggle the prefix blocker on or off for this server."""
        if not ctx.guild:
            await ctx.send("This command can only be used in a server.")
            return

        current = await self.config.guild(ctx.guild).enabled()
        new_state = not current
        await self.config.guild(ctx.guild).enabled.set(new_state)
        
        status = "enabled" if new_state else "disabled"
        await ctx.send(f"Prefix blocker has been **{status}** in this server.")
