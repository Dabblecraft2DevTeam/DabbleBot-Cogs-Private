from redbot.core import commands, tasks
import discord

class VoiceChannelNameUpdater(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.channel_name_list = [
            "Welcome Room", "Chill Zone", "Lounge", "Gaming Room", "Music Time", "Study Area"
        ]
        self.current_name_index = 0
        self.voice_channel = None
        self.update_name.start()  # Start the periodic task

    @commands.Cog.listener()
    async def on_ready(self):
        # Find or create the voice channel
        guild = self.bot.guilds[0]  # Assuming the bot is only in one server
        self.voice_channel = discord.utils.get(guild.voice_channels, name=self.channel_name_list[self.current_name_index])
        if not self.voice_channel:
            self.voice_channel = await guild.create_voice_channel(self.channel_name_list[self.current_name_index])
        print(f"Voice Channel '{self.voice_channel.name}' is ready.")

    @tasks.loop(minutes=5)  # Update every 5 minutes (can be adjusted)
    async def update_name(self):
        if self.voice_channel:
            self.current_name_index = (self.current_name_index + 1) % len(self.channel_name_list)
            new_name = self.channel_name_list[self.current_name_index]
            if self.voice_channel.name != new_name:
                await self.voice_channel.edit(name=new_name)
                print(f"Updated voice channel name to: {new_name}")

    @commands.command()
    async def create_voice_channel(self, ctx):
        """Manually create a voice channel with the current name."""
        guild = ctx.guild
        self.voice_channel = await guild.create_voice_channel(self.channel_name_list[self.current_name_index])
        await ctx.send(f"Voice channel created: {self.voice_channel.name}")

    @commands.command()
    async def set_channel_names(self, ctx, *names):
        """Set custom list of channel names."""
        if names:
            self.channel_name_list = list(names)
            await ctx.send(f"Updated channel names to: {', '.join(self.channel_name_list)}")
        else:
            await ctx.send("Please provide a list of names.")

    @commands.command()
    async def stop_name_update(self, ctx):
        """Stop the name updating task."""
        self.update_name.stop()
        await ctx.send("Voice channel name updating stopped.")

    @commands.command()
    async def start_name_update(self, ctx):
        """Start the name updating task."""
        self.update_name.start()
        await ctx.send("Voice channel name updating started.")

async def setup(bot):
    await bot.add_cog(VoiceChannelNameUpdater(bot))