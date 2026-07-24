from redbot.core import commands, Config
from discord.ext import tasks
import discord

class NameUpdate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=847392184, force_registration=True)
        default_guild = {
            "channel_names": [
                "Welcome Room", "Chill Zone", "Lounge", "Gaming Room", "Music Time", "Study Area"
            ],
            "current_index": 0,
            "voice_channel_id": None
        }
        self.config.register_guild(**default_guild)
        self.update_name.start()  # Start the periodic task

    def cog_unload(self):
        self.update_name.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        # We handle channel initialization on a per-guild basis instead of self.bot.guilds[0]
        pass

    @tasks.loop(minutes=5)  # Update every 5 minutes (can be adjusted)
    async def update_name(self):
        for guild in self.bot.guilds:
            try:
                channel_id = await self.config.guild(guild).voice_channel_id()
                if not channel_id: continue

                channel = guild.get_channel(channel_id)
                if not channel: continue

                names = await self.config.guild(guild).channel_names()
                if not names: continue

                current_index = await self.config.guild(guild).current_index()
                next_index = (current_index + 1) % len(names)

                # Security: Enforce 100 character limit for Discord channel names to prevent DoS
                new_name = names[next_index][:100]

                if channel.name != new_name:
                    await channel.edit(name=new_name)

                await self.config.guild(guild).current_index.set(next_index)
            except discord.HTTPException:
                pass # Fail securely without leaking info, while only catching expected Discord API errors
            except Exception:
                pass # Catch everything else defensively

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_channels=True)
    async def create_voice_channel(self, ctx):
        """Manually create a voice channel with the current name."""
        guild = ctx.guild
        names = await self.config.guild(guild).channel_names()
        current_index = await self.config.guild(guild).current_index()

        # Security: Enforce 100 character limit and prevent IndexError if list was shrunk
        safe_index = current_index if (names and current_index < len(names)) else 0
        new_name = names[safe_index][:100] if names else "Voice Channel"

        try:
            voice_channel = await guild.create_voice_channel(new_name)
            await self.config.guild(guild).voice_channel_id.set(voice_channel.id)
            await ctx.send(f"Voice channel created: {voice_channel.name}")
        except discord.Forbidden:
            await ctx.send("I do not have permission to create a voice channel.")

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_channels=True)
    async def set_channel_names(self, ctx, *names):
        """Set custom list of channel names."""
        if names:
            # Enforce 100 char limit at save time too
            safe_names = [name[:100] for name in names]
            await self.config.guild(ctx.guild).channel_names.set(list(safe_names))
            await ctx.send(f"Updated channel names to: {', '.join(safe_names)}")
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
    await bot.add_cog(NmaeUpdate(bot))