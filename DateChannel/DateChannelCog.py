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
            # Look for a channel with a specific name, or create one if it doesn't exist
            channel = discord.utils.get(guild.voice_channels, name="date-channel")

            if channel:
                try:
                    # Update the channel name with the current date
                    await channel.edit(name=f"date-{date_str}")
                    print(f"Updated channel {channel.name} to date-{date_str} in guild {guild.name}")
                except discord.Forbidden:
                    print(f"Bot does not have permission to edit the channel in guild {guild.name}.")
                except discord.HTTPException as e:
                    print(f"Failed to update channel name in guild {guild.name}: {e}")
            else:
                print(f"No 'date-channel' found in guild {guild.name}. Skipping update.")

    @channel_update.before_loop
    async def before_channel_update(self):
        """Wait until midnight to run the task for the first time."""
        now = datetime.datetime.now()
        next_midnight = datetime.datetime.combine(now.date(), datetime.time(0, 0)) + datetime.timedelta(days=1)
        wait_time = (next_midnight - now).total_seconds()
        await asyncio.sleep(wait_time)  # Sleep until midnight

    @commands.command()
    async def create_date_channel(self, ctx):
        """Create a 'date-channel' if it does not exist in the current guild."""
        guild = ctx.guild
        existing_channel = discord.utils.get(guild.voice_channels, name="date-channel")

        if existing_channel:
            await ctx.send("A channel named 'date-channel' already exists.")
        else:
            # Create the channel if it doesn't exist
            try:
                new_channel = await guild.create_voice_channel("date-channel")
                await ctx.send(f"Created new voice channel: {new_channel.name}")
            except discord.Forbidden:
                await ctx.send("I do not have permission to create a channel in this guild.")
            except discord.HTTPException as e:
                await ctx.send(f"Failed to create channel: {e}")

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
