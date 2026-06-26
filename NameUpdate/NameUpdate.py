from redbot.core import commands, Config
from discord.ext import tasks
import discord

class NameUpdate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Security: Use Config to prevent cross-guild state contamination (IDOR)
        self.config = Config.get_conf(self, identifier=837264823, force_registration=True)
        self.config.register_guild(
            names=["Welcome Room", "Chill Zone", "Lounge", "Gaming Room", "Music Time", "Study Area"],
            index=0,
            channel_id=None
        )
        self.update_name.start()

    def cog_unload(self):
        self.update_name.cancel()

    @tasks.loop(minutes=5)
    async def update_name(self):
        for guild in self.bot.guilds:
            data = await self.config.guild(guild).all()
            channel = guild.get_channel(data["channel_id"]) if data["channel_id"] else None
            if channel and data["names"]:
                idx = (data["index"] + 1) % len(data["names"])
                await self.config.guild(guild).index.set(idx)
                new_name = data["names"][idx][:100]  # Prevent API error (100 char limit)
                if channel.name != new_name:
                    try:
                        await channel.edit(name=new_name)
                    except discord.HTTPException:
                        pass

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_channels=True)
    async def create_voice_channel(self, ctx):
        """Manually create a voice channel with the current name."""
        data = await self.config.guild(ctx.guild).all()
        if data["names"]:
            channel = await ctx.guild.create_voice_channel(data["names"][data["index"]][:100])
            await self.config.guild(ctx.guild).channel_id.set(channel.id)
            await ctx.send(f"Voice channel created: {channel.name}")

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_channels=True)
    async def set_channel_names(self, ctx, *names):
        """Set custom list of channel names."""
        if names:
            # Security: Validate input length
            valid_names = [n[:100] for n in names]
            await self.config.guild(ctx.guild).names.set(valid_names)
            await self.config.guild(ctx.guild).index.set(0)
            await ctx.send(f"Updated channel names to: {', '.join(valid_names)}")
        else:
            await ctx.send("Please provide a list of names.")

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_channels=True)
    async def stop_name_update(self, ctx):
        """Stop the name updating task."""
        self.update_name.stop()
        await ctx.send("Voice channel name updating stopped.")

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_channels=True)
    async def start_name_update(self, ctx):
        """Start the name updating task."""
        self.update_name.start()
        await ctx.send("Voice channel name updating started.")

async def setup(bot):
    await bot.add_cog(NameUpdate(bot))