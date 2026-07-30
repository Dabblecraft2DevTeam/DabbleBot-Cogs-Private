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

    async def red_delete_data_for_user(self, *, requester: str, user_id: int):
        """This cog does not store any user data."""
        return

    async def red_get_data_for_user(self, *, user_id: int):
        """This cog does not store any user data."""
        return {}

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

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
            
        if not message.guild:
            return

        # Skip if author is a bot owner
        if await self.bot.is_owner(message.author):
            return

        enabled = await self.config.guild(message.guild).enabled()
        if not enabled:
            return

        # Get valid prefixes for this guild
        prefixes = await self.bot.get_valid_prefixes(message.guild)
        
        # We don't want to block mention prefixes.
        mention1 = f"<@{self.bot.user.id}> "
        mention2 = f"<@!{self.bot.user.id}> "
        
        is_prefix = False
        for prefix in prefixes:
            if prefix in (mention1, mention2):
                continue
            if message.content.startswith(prefix):
                is_prefix = True
                break
                
        if is_prefix:
            # Delete the original prefix command message
            try:
                await message.delete()
            except discord.Forbidden:
                pass
            except discord.NotFound:
                pass
            except discord.HTTPException:
                pass
            
            # Send brief message telling the user to use /db or @DabbleBot instead
            try:
                await message.channel.send(
                    f"{message.author.mention}, please use slash commands (like `/db`) or mention the bot (`@DabbleBot`) instead of using prefix commands.",
                    delete_after=10
                )
            except discord.Forbidden:
                pass
            except discord.HTTPException:
                pass
