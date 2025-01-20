import discord
from redbot.core import commands
from discord.ext import tasks
import datetime
import asyncio

class DateChannelCog(commands.Cog):
    """Cog that updates a voice channel name with the current date in all guilds."""

    def __init__(self, bot):
        self.bot = bot
        self.channel_update.start()  # Start the background task

    def cog_unload(self):
        self.channel_update.cancel()  # Ensure task stops when cog is unloaded

    @tasks.loop(hours=24)
    async def channel_update(self):
        """Background task to update the voice channel name every day in all guilds."""
        now = datetime.datetime.now()
        date_str = now.strftime("%m/%d/%Y")  # Format as MM/DD/YYYY

        # Loop through all the guilds the bot is in
        for guild in self.bot.guilds:
            # Placeholder condition: look for a channel starting with 'date-'
            channel = discord.utils.get(guild.voice_channels, name=lambda name: name and name.endswith("date-channel"))

            if channel:
                try:
                    await channel.edit(name=f"date-{date_str}")  # Prefix the date with 'date-'
                    print(f"Updated channel {channel.name} to date-{date_str} in guild {guild.name}")
                except discord.Forbidden:
                    print(f"Bot does not have permission to edit the channel in guild {guild.name}.")
                except discord.HTTPException as e:
                    print(f"Failed to update channel name in guild {guild.name}: {e}")

    @channel_update.before_loop
    async def before_channel_update(self):
        """Wait until midnight to run the task for the first time."""
        now = datetime.datetime.now()
        next_midnight = datetime.datetime.combine(now.date(), datetime.time(0, 0)) + datetime.timedelta(days=1)
        wait_time = (next_midnight - now).total_seconds()
        await asyncio.sleep(wait_time)  # Sleep until midnight

    @commands.command()
    async def start_date_update(self, ctx):
        """Manually trigger the first update of the voice channel."""
        await self.channel_update()
        await ctx.send("Date update task has been started.")

    @commands.command()
    async def stop_date_update(self, ctx):
        """Stop the background task."""
        self.channel_update.cancel()
        await ctx.send("Date update task has been stopped.")
