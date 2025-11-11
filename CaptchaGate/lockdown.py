import asyncio
import time
import discord
from redbot.core import commands
from redbot.core.commands import Context
from redbot.core.bot import Red
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    # This import is only for type hinting the main cog class if needed
    from .captchagate import CaptchaGate 

# --- Lockdown Mixin Class ---

class LockdownMixin:
    """
    Mixin class containing commands and logic for the CaptchaGate Lockdown feature.
    This class is mixed into the main CaptchaGate cog.
    """
    
    # Placeholder __init__ for MRO and attribute type hints
    def __init__(self, *args):
        # These are attributes defined in the main CaptchaGate class, 
        # which this mixin needs access to.
        self.bot: Red
        self.config: commands.Config
        self.log_action: callable
        self._send_captcha: callable
        self.active_captchas: dict

    # ----------------------------------------------------------------
    # --- Helper Function (Called when lockdown is lifted) ---
    # ----------------------------------------------------------------
    
    async def _process_lockdown_queue(self, guild: discord.Guild, context_channel: discord.TextChannel):
        """Processes the queue of users who joined during lockdown."""
        lockdown_users = await self.config.guild(guild).lockdown_users()
        if not lockdown_users:
            await context_channel.send("Queue is empty. No users to process.")
            return

        count = len(lockdown_users)
        await context_channel.send(f"Processing **{count}** queued members. This may take a moment.")
        await self.log_action(f"Processing {count} users from lockdown queue.", guild)

        # Clear the queue immediately to prevent issues on bot restart
        await self.config.guild(guild).lockdown_users.set({}) 

        for user_id_str, join_time in lockdown_users.items():
            user_id = int(user_id_str)
            member = guild.get_member(user_id)
            
            # Check if the user is still in the server
            if member:
                self.active_captchas[member.id] = {
                    "start_time": time.time(),
                    "attempts": 0,
                    "message_id": None, 
                    "public_message_id": None,
                    "error_message_id": None, 
                    "channel_id": None, 
                }
                
                await self._send_captcha(member)
                await asyncio.sleep(1)
            else:
                await self.log_action(f"Skipped queued user ID {user_id} - user left the server.", guild)

        await self.log_action("Lockdown queue processing complete.", guild)

    
    # ----------------------------------------------------------------
    # --- Command Method (Method to be registered as a command) ---
    # ----------------------------------------------------------------

    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def captchaset_lockdown(self, ctx: Context, state: bool):
        """
        Toggles the server lockdown mode.
        
        When enabled (`True`), new users are placed in a queue and a message is posted.
        When disabled (`False`), the queue is processed and CAPTCHAs are sent out to queued users.

        Arguments:
        - <state>: Must be `True` to enable lockdown or `False` to disable it.
                   (Example: `[p]captchaset lockdown True`)
        """
        current_state = await self.config.guild(ctx.guild).lockdown_enabled()
        captcha_channel_id = await self.config.guild(ctx.guild).captcha_channel()
        channel = ctx.guild.get_channel(captcha_channel_id)
        
        if not channel:
            return await ctx.send("❌ You must set a `captcha_channel` before using lockdown features.")
            
        if state == current_state:
            status = "enabled" if state else "disabled"
            return await ctx.send(f"❌ Lockdown is already **{status}**.")

        if state: # ENABLE LOCKDOWN
            await self.config.guild(ctx.guild).lockdown_enabled.set(True)
            
            lockdown_embed = discord.Embed(
                title="🔒 Server Verification Locked Down",
                description="The server is currently experiencing high traffic or a raid. Verification has been temporarily paused. Please be patient, you will automatically receive the CAPTCHA when the lockdown is lifted.",
                color=discord.Color.red()
            )
            try:
                lockdown_message = await channel.send(embed=lockdown_embed)
                await self.config.guild(ctx.guild).lockdown_message_id.set(lockdown_message.id)
                await ctx.send("✅ Server lockdown **ENABLED**. New users will be queued.")
            except discord.Forbidden:
                await self.config.guild(ctx.guild).lockdown_enabled.set(False) # Rollback
                await ctx.send("❌ Failed to send lockdown message in the CAPTCHA channel. Check bot permissions. Lockdown cancelled.")

        else: # DISABLE LOCKDOWN
            await self.config.guild(ctx.guild).lockdown_enabled.set(False)
            await ctx.send("✅ Server lockdown **DISABLED**. Processing user queue...")
            
            # 1. Clean up the announcement message
            message_id = await self.config.guild(ctx.guild).lockdown_message_id()
            if message_id:
                try:
                    message = await channel.fetch_message(message_id)
                    await message.delete()
                    await self.config.guild(ctx.guild).lockdown_message_id.set(None)
                except Exception:
                    await ctx.send("⚠️ Could not delete the previous lockdown announcement message. Moving on.")
                    
            # 2. Process the queued users
            await self._process_lockdown_queue(ctx.guild, ctx.channel)
            
            await ctx.send("✅ User queue processed. CAPTCHAs have been initiated for new members.")

    # ----------------------------------------------------------------
    # --- Command Attachment Function (Callable by the main cog) ---
    # ----------------------------------------------------------------
    def _add_lockdown_command(self, captchaset_group: commands.Group):
        """Attaches the lockdown command (method) to the main group."""
        captchaset_group.add_command(
            commands.Command(
                self.captchaset_lockdown,
                name="lockdown"
            )
        )
# --- END LockdownMixin ---