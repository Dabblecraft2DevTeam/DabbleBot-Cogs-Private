from redbot.core import commands, Config
from discord.ext import tasks
import discord

class NameUpdate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9843729482, force_registration=True)
        default_guild = {
            "channel_name_list": ["Welcome Room", "Chill Zone", "Lounge", "Gaming Room", "Music Time", "Study Area"],
            "current_name_index": 0,
            "voice_channel_id": None,
            "is_running": True
        }
        self.config.register_guild(**default_guild)
        self.update_name.start()  # Start the periodic task

    def cog_unload(self):
        self.update_name.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        # Find or create the voice channel for each guild securely
        for guild in self.bot.guilds:
            config_data = await self.config.guild(guild).all()
            if not config_data["is_running"]:
                continue

            channel_name_list = config_data["channel_name_list"]
            current_index = config_data["current_name_index"]
            channel_id = config_data["voice_channel_id"]

            voice_channel = guild.get_channel(channel_id) if channel_id else None
            if not voice_channel:
                voice_channel = discord.utils.get(guild.voice_channels, name=channel_name_list[current_index])
                if not voice_channel:
                    try:
                        voice_channel = await guild.create_voice_channel(channel_name_list[current_index])
                        await self.config.guild(guild).voice_channel_id.set(voice_channel.id)
                        print(f"Voice Channel '{voice_channel.name}' created in {guild.name}.")
                    except discord.Forbidden:
                        print(f"Failed to create channel in {guild.name}: Missing permissions.")
                else:
                     await self.config.guild(guild).voice_channel_id.set(voice_channel.id)

    @tasks.loop(minutes=5)  # Update every 5 minutes (can be adjusted)
    async def update_name(self):
        for guild in self.bot.guilds:
            config_data = await self.config.guild(guild).all()
            if not config_data["is_running"]:
                continue

            channel_name_list = config_data["channel_name_list"]
            current_index = config_data["current_name_index"]
            channel_id = config_data["voice_channel_id"]

            voice_channel = guild.get_channel(channel_id) if channel_id else None

            if voice_channel:
                new_index = (current_index + 1) % len(channel_name_list)
                new_name = channel_name_list[new_index]

                # Always update the index in the config so it keeps rotating
                await self.config.guild(guild).current_name_index.set(new_index)

                if voice_channel.name != new_name:
                    try:
                        await voice_channel.edit(name=new_name)
                        print(f"Updated voice channel name to: {new_name} in {guild.name}")
                    except discord.Forbidden:
                        pass
                    except discord.HTTPException:
                        pass

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_channels=True)
    async def create_voice_channel(self, ctx):
        """Manually create a voice channel with the current name."""
        guild = ctx.guild
        config_data = await self.config.guild(guild).all()
        channel_name_list = config_data["channel_name_list"]
        current_index = config_data["current_name_index"]

        try:
            voice_channel = await guild.create_voice_channel(channel_name_list[current_index])
            await self.config.guild(guild).voice_channel_id.set(voice_channel.id)
            await ctx.send(f"Voice channel created: {voice_channel.name}")
        except discord.Forbidden:
            await ctx.send("I don't have permission to create voice channels.")

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_channels=True)
    async def set_channel_names(self, ctx, *names):
        """Set custom list of channel names."""
        if names:
            await self.config.guild(ctx.guild).channel_name_list.set(list(names))
            await ctx.send(f"Updated channel names to: {', '.join(names)}")
        else:
            await ctx.send("Please provide a list of names.")

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_channels=True)
    async def stop_name_update(self, ctx):
        """Stop the name updating task for this server."""
        await self.config.guild(ctx.guild).is_running.set(False)
        await ctx.send("Voice channel name updating stopped for this server.")

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_channels=True)
    async def start_name_update(self, ctx):
        """Start the name updating task for this server."""
        await self.config.guild(ctx.guild).is_running.set(True)
        await ctx.send("Voice channel name updating started for this server.")

async def setup(bot):
    await bot.add_cog(NameUpdate(bot))