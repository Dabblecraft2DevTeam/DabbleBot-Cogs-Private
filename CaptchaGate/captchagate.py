import asyncio
import time
import discord
import random
import string
import logging

from redbot.core import commands, Config
from redbot.core.commands import Context
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import pagify

from .lockdown import LockdownMixin 

# Define the logger for standard Python logging
log = logging.getLogger("red.dabblecraft2devteam.captchagate") 

# Define default settings
DEFAULT_GUILD = {
    "unverified_role": None,
    "verified_role": None,
    "captcha_channel": None,
    "welcome_channel": None,
    "log_channel": None,
    "captcha_timeout": 300,  # 5 minutes
    "captcha_attempts": 3,
    "lockdown_enabled": False,
    "lockdown_message_id": None,
    "lockdown_users": {}, # {user_id: join_timestamp}
    "min_captcha_length": 5,
    "max_captcha_length": 8,
}

class CaptchaGate(
    LockdownMixin,
    commands.Cog
):
    """
    Automatic CAPTCHA verification system to prevent bot raids and spam.
    This cog handles user verification, role assignment, and server lockdown.
    """
    
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD)
        
        # Stores active CAPTCHAs: {user_id: {"code": "...", "start_time": ..., "attempts": ...}}
        self.active_captchas = {}
        
        # Attach the lockdown command from the Mixin to the primary group
        self._add_lockdown_command(self.captchaset)


    # ----------------------------------------------------------------
    # --- Core Utility Functions ---
    # ----------------------------------------------------------------
    
    async def log_action(self, message: str, guild: discord.Guild):
        """Sends a message to the configured log channel, if set."""
        log_channel_id = await self.config.guild(guild).log_channel()
        
        log.info(f"[{guild.name}] {message}") 
        
        if log_channel_id:
            channel = guild.get_channel(log_channel_id)
            if channel:
                try:
                    for page in pagify(message):
                        await channel.send(page)
                except discord.Forbidden:
                    log.warning(f"Could not send log message to channel {channel.id} in guild {guild.id}. Check permissions.")

    def _generate_captcha_code(self, guild: discord.Guild) -> str:
        """Generates a random alphanumeric CAPTCHA code."""
        length_future = self.config.guild(guild).min_captcha_length()
        length = random.randint(
            length_future,
            self.config.guild(guild).max_captcha_length()
        )
        characters = string.ascii_uppercase + string.digits
        return ''.join(random.choice(characters) for _ in range(length))

    async def _send_captcha(self, member: discord.Member):
        """
        Generates a CAPTCHA code, stores it, and sends the prompt to the user via DM.
        This function assumes the member is already in self.active_captchas.
        """
        guild = member.guild
        code = self._generate_captcha_code(guild)

        # Update the active_captchas data with the new code
        self.active_captchas[member.id]["code"] = code
        self.active_captchas[member.id]["start_time"] = time.time()
        
        # Load config settings
        timeout = await self.config.guild(guild).captcha_timeout()
        captcha_channel_id = await self.config.guild(guild).captcha_channel()
        channel = guild.get_channel(captcha_channel_id)
        
        if not channel:
            await self.log_action(f"❌ Failed to send CAPTCHA to {member.name}. Captcha channel not set.", guild)
            return

        dm_embed = discord.Embed(
            title="🔒 Verification Required",
            description=(
                f"You must complete this CAPTCHA within **{timeout} seconds** "
                f"to gain access to **{guild.name}**."
                f"\n\n**Please type the following code into this chat:**"
            ),
            color=discord.Color.blue()
        )
        dm_embed.add_field(name="CAPTCHA Code", value=f"```\n{code}\n```", inline=False)
        dm_embed.set_footer(text=f"Attempts remaining: {await self.config.guild(guild).captcha_attempts()}")

        try:
            # Send DM and store message ID for later deletion/tracking
            dm_message = await member.send(embed=dm_embed)
            self.active_captchas[member.id]["message_id"] = dm_message.id
            self.active_captchas[member.id]["channel_id"] = dm_message.channel.id
            
            # Send public message in the CAPTCHA channel
            public_message = await channel.send(f"Hey {member.mention}, please check your DMs to complete verification!", delete_after=timeout)
            self.active_captchas[member.id]["public_message_id"] = public_message.id

        except discord.Forbidden:
            # Handle case where DMs are disabled
            error_embed = discord.Embed(
                title="❌ DM Failed",
                description=(
                    f"{member.mention}, I could not send you a CAPTCHA because your DMs are closed."
                    f"Please enable DMs from server members, then type your CAPTCHA response here:"
                ),
                color=discord.Color.red()
            )
            # Publicly send the code for closed DMs
            error_embed.add_field(name="CAPTCHA Code", value=f"```\n{code}\n```", inline=False)
            
            error_message = await channel.send(embed=error_embed, delete_after=timeout)
            self.active_captchas[member.id]["error_message_id"] = error_message.id
            self.active_captchas[member.id]["public_message_id"] = error_message.id # Use the same ID for cleanup
            
            await self.log_action(f"⚠️ User {member.name} has DMs disabled. Sent CAPTCHA publicly in {channel.mention}.", guild)


    async def _process_verified_user(self, member: discord.Member, source: str = "CAPTCHA"):
        """Removes unverified role, adds verified role, and logs the action."""
        
        guild = member.guild
        config_group = self.config.guild(guild)
        
        unverified_role_id = await config_group.unverified_role()
        verified_role_id = await config_group.verified_role()
        welcome_channel_id = await config_group.welcome_channel()

        # 1. Role Removal/Assignment
        if unverified_role_id:
            unverified_role = guild.get_role(unverified_role_id)
            if unverified_role and unverified_role in member.roles:
                try:
                    await member.remove_roles(unverified_role)
                except discord.Forbidden:
                    await self.log_action(f"❌ Failed to remove unverified role from {member.name}.", guild)

        if verified_role_id:
            verified_role = guild.get_role(verified_role_id)
            if verified_role:
                try:
                    await member.add_roles(verified_role)
                except discord.Forbidden:
                    await self.log_action(f"❌ Failed to assign verified role to {member.name}.", guild)
        
        # 2. Logging and Welcome
        await self.log_action(f"✅ User {member.name} verified successfully via {source}.", guild)
        
        if welcome_channel_id:
            welcome_channel = guild.get_channel(welcome_channel_id)
            if welcome_channel:
                try:
                    await welcome_channel.send(f"Welcome {member.mention}! You have been verified and now have access to the server.")
                except discord.Forbidden:
                    log.warning(f"Failed to send welcome message to channel {welcome_channel_id}.")

    
    # ----------------------------------------------------------------
    # --- Listeners (Event Handlers) ---
    # ----------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Handles new member joins."""
        guild = member.guild
        config_group = self.config.guild(guild)
        
        if member.bot:
            return

        unverified_role_id = await config_group.unverified_role()
        is_lockdown = await config_group.lockdown_enabled()

        if unverified_role_id:
            unverified_role = guild.get_role(unverified_role_id)
            if unverified_role:
                try:
                    # Assign the unverified role
                    await member.add_roles(unverified_role)
                except discord.Forbidden:
                    await self.log_action(f"❌ Could not assign unverified role to {member.name}. Check bot permissions.", guild)
                    return

                if is_lockdown:
                    # Add user to lockdown queue
                    lockdown_users = await config_group.lockdown_users()
                    lockdown_users[str(member.id)] = time.time()
                    await config_group.lockdown_users.set(lockdown_users)
                    await self.log_action(f"⚠️ User {member.name} joined during lockdown. Added to queue.", guild)
                else:
                    # Initiate CAPTCHA
                    await self.log_action(f"🚀 User {member.name} joined. Initiating CAPTCHA.", guild)
                    self.active_captchas[member.id] = {
                        "start_time": time.time(),
                        "attempts": 0,
                        "code": None,
                        "message_id": None,
                        "public_message_id": None,
                        "error_message_id": None, 
                        "channel_id": None, 
                    }
                    await self._send_captcha(member)
            else:
                await self.log_action(f"Configuration Error: Unverified role ID {unverified_role_id} not found.", guild)


    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Handles user responses in DMs."""
        if message.author.bot or message.guild is not None:
            return

        user_id = message.author.id
        
        if user_id in self.active_captchas:
            captcha_data = self.active_captchas[user_id]
            # Get the guild this CAPTCHA is for (assuming one active CAPTCHA per user)
            member = self.bot.get_user(user_id)
            if not member or not member.mutual_guilds:
                del self.active_captchas[user_id]
                return
            
            # Find the guild where the user has the unverified role (simplification)
            guild = next((g for g in member.mutual_guilds if await self.config.guild(g).unverified_role()), None)

            if guild:
                config_group = self.config.guild(guild)
                max_attempts = await config_group.captcha_attempts()
                
                # Check for timeout (simple check here)
                timeout = await config_group.captcha_timeout()
                if time.time() - captcha_data["start_time"] > timeout:
                    del self.active_captchas[user_id]
                    await message.channel.send("❌ Verification timed out.")
                    await self.log_action(f"❌ User {member.name} timed out on CAPTCHA.", guild)
                    return
                
                # 1. Successful CAPTCHA
                if message.content.strip().upper() == captcha_data["code"].upper():
                    # Cleanup DM message if necessary
                    try:
                        if captcha_data.get("message_id") and captcha_data["channel_id"] == message.channel.id:
                            dm_message = await message.channel.fetch_message(captcha_data["message_id"])
                            await dm_message.delete()
                    except Exception:
                        pass
                    
                    # Cleanup public message
                    if captcha_data.get("public_message_id"):
                        try:
                            captcha_channel_id = await config_group.captcha_channel()
                            channel = guild.get_channel(captcha_channel_id)
                            if channel:
                                public_message = await channel.fetch_message(captcha_data["public_message_id"])
                                await public_message.delete()
                        except Exception:
                            pass
                    
                    del self.active_captchas[user_id]
                    await self._process_verified_user(guild.get_member(user_id) or member)
                    await message.channel.send("✅ Verification successful! You now have access to the server.")
                    return

                # 2. Failed CAPTCHA
                else:
                    captcha_data["attempts"] += 1
                    
                    # Too many attempts
                    if captcha_data["attempts"] >= max_attempts:
                        del self.active_captchas[user_id]
                        await message.channel.send(f"❌ Verification failed. Too many incorrect attempts. You have been removed from {guild.name}.")
                        await self.log_action(f"❌ User {member.name} failed CAPTCHA and was removed.", guild)
                        
                        member_guild = guild.get_member(user_id)
                        if member_guild:
                            try:
                                # Clean up unverified role before kicking (best practice)
                                unverified_role_id = await config_group.unverified_role()
                                unverified_role = guild.get_role(unverified_role_id)
                                if unverified_role:
                                    await member_guild.remove_roles(unverified_role)

                                await member_guild.kick(reason="Failed CAPTCHA verification.")
                            except discord.Forbidden:
                                await self.log_action(f"❌ Could not kick {member.name} after CAPTCHA failure. Check bot permissions.", guild)
                        
                        return
                        
                    # Send new CAPTCHA prompt
                    new_code = self._generate_captcha_code(guild)
                    captcha_data["code"] = new_code
                    
                    new_embed = discord.Embed(
                        title="❌ Incorrect Code",
                        description=f"Try again. You have **{max_attempts - captcha_data['attempts']} attempts** remaining.",
                        color=discord.Color.orange()
                    )
                    new_embed.add_field(name="New CAPTCHA Code", value=f"```\n{new_code}\n```", inline=False)
                    
                    # Re-send or edit DM
                    if captcha_data.get("message_id"):
                        try:
                            dm_channel = self.bot.get_channel(captcha_data["channel_id"])
                            dm_message = await dm_channel.fetch_message(captcha_data["message_id"])
                            await dm_message.edit(embed=new_embed)
                        except Exception:
                            await message.channel.send(embed=new_embed)
                    else:
                        await message.channel.send(embed=new_embed)


    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Clean up active CAPTCHA and queue data when a member leaves."""
        
        # 1. Check and clean up active CAPTCHA (NEW LOGGING HERE)
        if member.id in self.active_captchas:
            log_message = (
                f"⚠️ **CAPTCHA Clean-up:** Member {member.mention} (`{member.name}` / `{member.id}`) "
                f"left the server while a CAPTCHA was active. Cleaned up their data."
            )
            await self.log_action(log_message, member.guild)
            
            captcha_data = self.active_captchas.pop(member.id, None)
            
            # Attempt to delete the public message
            if captcha_data and captcha_data.get("public_message_id"):
                captcha_channel_id = await self.config.guild(member.guild).captcha_channel()
                channel = member.guild.get_channel(captcha_channel_id)
                if channel:
                    try:
                        message = await channel.fetch_message(captcha_data["public_message_id"])
                        await message.delete()
                    except discord.NotFound:
                        pass
                    except Exception as e:
                        log.warning(f"Failed to delete public CAPTCHA message for {member.id}: {e}")

        # 2. Check and clean up lockdown queue data (NEW LOGGING HERE)
        lockdown_users = await self.config.guild(member.guild).lockdown_users()
        if str(member.id) in lockdown_users:
            
            log_message = (
                f"🗑️ **Queue Clean-up:** Member {member.mention} (`{member.name}` / `{member.id}`) "
                f"left the server while they were in the lockdown queue. Removed from queue."
            )
            await self.log_action(log_message, member.guild)
            
            del lockdown_users[str(member.id)]
            await self.config.guild(member.guild).lockdown_users.set(lockdown_users)


    # ----------------------------------------------------------------
    # --- Commands (Configuration Group) ---
    # ----------------------------------------------------------------

    @commands.group(name="captchaset", aliases=["cs"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def captchaset(self, ctx: Context):
        """Base command for CaptchaGate configuration."""
        pass
    
    # --- Configuration Subcommands ---

    @captchaset.command(name="unverifiedrole")
    async def cs_unverified_role(self, ctx: Context, role: discord.Role):
        """Set the role new members receive for unverified status."""
        if role.id == await self.config.guild(ctx.guild).verified_role():
            return await ctx.send("❌ The unverified role cannot be the same as the verified role.")
        await self.config.guild(ctx.guild).unverified_role.set(role.id)
        await ctx.send(f"✅ Unverified role set to **{role.name}**.")

    @captchaset.command(name="verifiedrole")
    async def cs_verified_role(self, ctx: Context, role: discord.Role):
        """Set the role members receive upon successful verification."""
        if role.id == await self.config.guild(ctx.guild).unverified_role():
            return await ctx.send("❌ The verified role cannot be the same as the unverified role.")
        await self.config.guild(ctx.guild).verified_role.set(role.id)
        await ctx.send(f"✅ Verified role set to **{role.name}**.")

    @captchaset.command(name="channel")
    async def cs_captcha_channel(self, ctx: Context, channel: discord.TextChannel):
        """Set the channel where verification announcements and public error messages are sent."""
        await self.config.guild(ctx.guild).captcha_channel.set(channel.id)
        await ctx.send(f"✅ CAPTCHA channel set to {channel.mention}.")

    @captchaset.command(name="logchannel")
    async def cs_log_channel(self, ctx: Context, channel: discord.TextChannel):
        """Set the channel for logging verification activity, cleanups, and lockdown state changes."""
        await self.config.guild(ctx.guild).log_channel.set(channel.id)
        await ctx.send(f"✅ Log channel set to {channel.mention}.")

    @captchaset.command(name="welcometo")
    async def cs_welcome_channel(self, ctx: Context, channel: discord.TextChannel):
        """Set the channel where a welcome message is sent after a user is verified."""
        await self.config.guild(ctx.guild).welcome_channel.set(channel.id)
        await ctx.send(f"✅ Welcome channel set to {channel.mention}.")

    @captchaset.command(name="timeout")
    async def cs_timeout(self, ctx: Context, seconds: int):
        """Set the time (in seconds) a user has to complete the CAPTCHA."""
        if seconds < 30 or seconds > 600:
            return await ctx.send("❌ Timeout must be between 30 and 600 seconds (5 to 10 minutes).")
        await self.config.guild(ctx.guild).captcha_timeout.set(seconds)
        await ctx.send(f"✅ CAPTCHA timeout set to **{seconds} seconds**.")

    @captchaset.command(name="attempts")
    async def cs_attempts(self, ctx: Context, count: int):
        """Set the maximum number of attempts a user gets before being kicked."""
        if count < 1 or count > 5:
            return await ctx.send("❌ Attempts must be between 1 and 5.")
        await self.config.guild(ctx.guild).captcha_attempts.set(count)
        await ctx.send(f"✅ CAPTCHA maximum attempts set to **{count}**.")

    @captchaset.command(name="length")
    async def cs_length(self, ctx: Context, min_length: int, max_length: int):
        """Set the minimum and maximum length for the randomly generated CAPTCHA code."""
        if min_length < 4 or max_length > 10 or min_length > max_length:
            return await ctx.send("❌ Invalid lengths. Minimum must be 4, maximum 10, and min must be less than or equal to max.")
        await self.config.guild(ctx.guild).min_captcha_length.set(min_length)
        await self.config.guild(ctx.guild).max_captcha_length.set(max_length)
        await ctx.send(f"✅ CAPTCHA length set to between **{min_length}** and **{max_length}** characters.")