import discord
from redbot.core import commands
import lavalink
import logging

log = logging.getLogger("red.musicstatus")

class MusicStatus(commands.Cog):
    """Automatically updates voice channel status to the currently playing track."""

    def __init__(self, bot):
        self.bot = bot
        # Register the Red-Lavalink event listener
        lavalink.register_event_listener(self.lavalink_event_handler)

    def cog_unload(self):
        # Cleanly unregister the listener when the cog is unloaded or reloaded
        lavalink.unregister_event_listener(self.lavalink_event_handler)

    async def lavalink_event_handler(self, player: lavalink.Player, event_type: lavalink.LavalinkEvents, extra):
        """Listens to internal Red-Lavalink events to trigger status updates."""
        
        # --- TRACK START ---
        if event_type == lavalink.LavalinkEvents.TRACK_START:
            channel = player.channel
            if not isinstance(channel, discord.VoiceChannel):
                return
            
            track = player.current
            if track:
                # Discord restricts voice channel statuses to 500 characters
                status_text = f"🎶 Now playing: {track.title}"[:500]
                try:
                    await channel.edit(status=status_text)
                    log.debug(f"Updated status in {channel.guild.name} to {track.title}")
                except discord.Forbidden:
                    log.warning(f"Missing permissions to set voice channel status in {channel.guild.name}.")
                except discord.HTTPException as e:
                    log.debug(f"Failed to update VC status (Possible rate limit): {e}")

        # --- QUEUE END OR BOT DISCONNECT ---
        elif event_type in (lavalink.LavalinkEvents.QUEUE_END, lavalink.LavalinkEvents.FORCED_DISCONNECT):
            channel = player.channel
            if isinstance(channel, discord.VoiceChannel):
                try:
                    await channel.edit(status=None)
                except Exception:
                    pass

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Clears the status if the bot is forcefully disconnected by a user."""
        if member == self.bot.user and before.channel is not None and after.channel is None:
            if isinstance(before.channel, discord.VoiceChannel):
                try:
                    await before.channel.edit(status=None)
                except Exception:
                    pass

async def setup(bot):
    await bot.add_cog(MusicStatus(bot))