import discord
from redbot.core import commands
from discord.ext import tasks
import datetime
import asyncio

class DateChannelCog(commands.Cog):
    """Cog that updates a voice channel name with the current date."""

    def __init__(self, bot):
        self.bot = bot
        self.channel_update.start()  # Start the background task

    def cog_unload(self):
        self.channel_update.cancel()  # Ensure task stops when cog is unloaded

    @tasks.loop(hours=24)
    async def channel_update(self):
        """Background task to update the voice channel name every day."""
        now = datetime.datetime.now()
        date_str = now.strftime("%m/%d/%Y")  # Format as MM/DD/YYYY

        guild = self.bot.get_guild(123456789012345678)  # Replace with your guild ID
        if not guild:
            return

        channel = discord.utils.get(guild.voice_channels, name="date-channel")  # Replace with your channel name or condition

        if channel:
            try:
                await channel.edit(name=date_str)
                print(f"Updated channel {channel.name} to {date_str}")
            except discord.Forbidden:
                print("Bot does not have permission to edit the channel.")
            except discord.HTTPException as e:
                print(f"Failed to update channel name: {e}")
    
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

    @commands.command()
    async def stop_date_update(self, ctx):
        """Stop the background task."""
        self.channel_update.cancel()
        await ctx.send("Date update task has been stopped.")