import asyncio
import time 
import random
from typing import Literal, Optional

import discord
from discord.ext import tasks 
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import humanize_list
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu

# Define the possible verification modes
VERIFICATION_MODES = Literal["PUBLIC", "DM", "PRIVATE_CHANNEL"]

# --- View for the Captcha Options ---

class CaptchaView(discord.ui.View):
    """
    A custom Discord View to handle the multiple-choice CAPTCHA buttons.
    Uses interaction_check to ensure only the target user can interact.
    """
    def __init__(self, cog, member: discord.Member, correct_answer: str, timeout: int, options: list[str]):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.member = member
        self.correct_answer = correct_answer
        self.message: Optional[discord.Message] = None 
        
        self._create_buttons(options)

    def _create_buttons(self, options: list[str]):
        """Creates a button for each option and assigns the callback."""
        random.shuffle(options)
        
        for option in options:
            button = discord.ui.Button(label=option, style=discord.ButtonStyle.secondary)
            
            # Using partial for callback to pass the option label
            button.callback = lambda interaction, label=option: self.process_answer(interaction, label)
            self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Only allows the target member to interact with the buttons."""
        if interaction.user == self.member:
            return True
        await interaction.response.send_message("🚫 This CAPTCHA is not for you. You must wait for your own verification.", ephemeral=True)
        return False

    async def on_timeout(self):
        """Called when the view's internal timeout expires."""
        self.stop()
        for item in self.children:
            item.disabled = True
        if self.message:
            await self.message.edit(view=self)

    async def process_answer(self, interaction: discord.Interaction, user_answer: str):
        """Handles a button click and processes the user's answer."""
        self.stop()
        for item in self.children:
            item.disabled = True
        if interaction.message:
            await interaction.message.edit(view=self)
        
        is_correct = user_answer == self.correct_answer

        if is_correct:
            await interaction.response.send_message("✅ **Success!** You passed the CAPTCHA.", ephemeral=True)
            await self.cog.grant_role_and_cleanup(self.member) 
        else:
            await interaction.response.send_message("❌ **Incorrect!** Please try again.", ephemeral=True)
            await self.cog.handle_failed_attempt(self.member)
        
        await self.cog.log_action(f"CAPTCHA attempt by {self.member.name} ({self.member.id}). Correct: **{is_correct}**", self.member.guild)


# --- View for DM Retry ---

class RetryView(discord.ui.View):
    """A view with a button to retry the CAPTCHA after enabling DMs."""
    def __init__(self, cog, member: discord.Member, timeout: int):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.member = member
        self.add_item(discord.ui.Button(label="I've Enabled DMs - Retry CAPTCHA", style=discord.ButtonStyle.success))
        self.children[0].callback = self.on_retry_click
        
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Only allows the target member to interact with the button."""
        if interaction.user == self.member:
            return True
        await interaction.response.send_message("🚫 This button is only for the person who needs to verify.", ephemeral=True)
        return False

    async def on_retry_click(self, interaction: discord.Interaction):
        """Callback when the user clicks the retry button."""
        self.stop()
        for item in self.children:
            item.disabled = True
        
        # Disable the button immediately
        await interaction.message.edit(view=self)
        
        # Notify the user privately and restart the process
        await interaction.response.send_message("Attempting to resend CAPTCHA via DM...", ephemeral=True)
        await self.cog.log_action(f"🔄 {self.member.name} requested CAPTCHA retry.", self.member.guild)
        
        # Restart the CAPTCHA process for the member
        await self.cog.handle_dm_retry(self.member, interaction.message)
        
    async def on_timeout(self):
        """Disable buttons on timeout."""
        for item in self.children:
            item.disabled = True
        if self.message:
            await self.message.edit(view=self)


# --- Main Cog Class ---

class CaptchaGate(commands.Cog):
    """
    A CAPTCHA system to verify new members with multiple delivery options.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=14092025, force_registration=True)
        
        default_guild = {
            "captcha_channel": None,
            "success_role": None,
            "log_channel": None,
            "kick_timeout": 300, 
            "max_attempts": 3,
            "challenges": {}, 
            "welcome_embed_title": "👋 Welcome New Member!", 
            "welcome_embed_desc": "Please wait a moment while we prepare your verification test...",
            "verification_mode": "PUBLIC", 
        }
        
        self.config.register_guild(**default_guild)
        
        # Tracks {member_id: {"start_time": float, "attempts": int, "message_id": int | "public_message_id": int | "channel_id": int | "error_message_id": int}}
        self.active_captchas = {} 
        self.kick_task = self.kick_timed_out_users.start()

    def cog_unload(self):
        self.kick_task.cancel()

# ----------------------------------------------------------------
# --- Utility Functions ---
# ----------------------------------------------------------------

    async def log_action(self, message: str, guild: Optional[discord.Guild] = None):
        """Helper function to output a log message to the configured channel."""
        if not guild: return
        channel_id = await self.config.guild(guild).log_channel()
        if channel_id:
            channel = guild.get_channel(channel_id)
            if channel:
                try:
                    await channel.send(f"**CAPTCHA LOG:** {message}")
                except discord.Forbidden:
                    pass 

    async def _delete_channel_messages(self, guild: discord.Guild, channel: discord.TextChannel, member_data: dict):
        """Helper to delete public CAPTCHA messages and welcome messages."""
        message_keys = ["message_id", "public_message_id", "error_message_id"]

        for key in message_keys:
            message_id = member_data.get(key)
            if message_id:
                try:
                    # Clear the message ID immediately from the data to prevent re-attempts at deletion
                    member_data[key] = None 
                    message = await channel.fetch_message(message_id)
                    await message.delete()
                except Exception:
                    pass 

    async def _delete_private_channel(self, member: discord.Member, channel_id: Optional[int]):
        """Helper to safely delete the private verification channel."""
        if not channel_id:
            return
        channel = member.guild.get_channel(channel_id)
        if channel:
            try:
                await channel.delete(reason="CaptchaGate verification complete/failed.")
            except Exception:
                await self.log_action(f"⚠️ Failed to delete private channel for {member.name}.", member.guild)
    
    async def _cleanup_messages_or_channel(self, member: discord.Member):
        """Handles cleanup based on the verification mode."""
        guild_settings = await self.config.guild(member.guild).all()
        mode = guild_settings["verification_mode"]
        member_data = self.active_captchas.get(member.id)
        
        if not member_data: return

        # All modes that use the public channel need the cleanup handled here.
        if mode in ["PUBLIC", "DM", "PRIVATE_CHANNEL"]:
            channel_id = guild_settings["captcha_channel"]
            channel = member.guild.get_channel(channel_id)
            if channel:
                # This handles public_message_id (PRIVATE_CHANNEL welcome message/PUBLIC captcha) 
                # and error_message_id (DM failure notification).
                await self._delete_channel_messages(member.guild, channel, member_data)
        
        if mode == "PRIVATE_CHANNEL":
            # This handles the deletion of the temporary private text channel.
            await self._delete_private_channel(member, member_data.get("channel_id"))

        self.active_captchas.pop(member.id, None)


    async def grant_role_and_cleanup(self, member: discord.Member):
        """Grants the success role and cleans up resources."""
        guild_settings = await self.config.guild(member.guild).all()
        role_id = guild_settings["success_role"]
        
        # Role Granting Logic
        if role_id:
            role = member.guild.get_role(role_id)
            if role and role < member.guild.me.top_role:
                try:
                    await member.add_roles(role, reason="Passed CaptchaGate verification.")
                    await self.log_action(f"✅ {member.name} passed the CAPTCHA.", member.guild)
                except Exception:
                    await self.log_action(f"⚠️ Error granting role to {member.name}.", member.guild)
            else:
                await self.log_action(f"⚠️ Success role not set or bot can't assign it.", member.guild)

        await self._cleanup_messages_or_channel(member)


    async def handle_failed_attempt(self, member: discord.Member):
        """Increments fail count and kicks user if max attempts are reached."""
        if member.id not in self.active_captchas: return
            
        guild_settings = await self.config.guild(member.guild).all()
        max_attempts = guild_settings["max_attempts"]
        self.active_captchas[member.id]["attempts"] += 1
        current_attempts = self.active_captchas[member.id]["attempts"]

        if current_attempts >= max_attempts:
            await self._kick_user(member, f"Exceeded maximum CAPTCHA attempts ({max_attempts}).")
        else:
            remaining = max_attempts - current_attempts
            await self.log_action(f"❌ {member.name} failed attempt {current_attempts}/{max_attempts}.", member.guild)
            await self._send_captcha(member)

    async def handle_dm_retry(self, member: discord.Member, error_message: discord.Message):
        """Cleans up the DM error message and restarts the CAPTCHA process for the user."""
        member_data = self.active_captchas.get(member.id)
        if not member_data: return

        # 1. Clear the error message from the channel
        try:
            # We don't use _delete_channel_messages here as we have the message object
            await error_message.delete()
        except Exception:
            pass
            
        # 2. Clear the tracked error ID and reset the kick timer
        member_data["error_message_id"] = None
        member_data["start_time"] = time.time() 
        
        # 3. Send the CAPTCHA again
        await self._send_captcha(member)


    async def _kick_user(self, member: discord.Member, reason: str):
        """Handles kicking a user and cleaning up resources."""
        try:
            await member.send(f"You have been kicked from **{member.guild.name}** because you failed to complete the CAPTCHA. Reason: `{reason}`")
        except discord.Forbidden:
            pass
            
        try:
            await member.kick(reason=f"CaptchaGate Kick: {reason}")
            await self.log_action(f"🔨 Kicked {member.name}. Reason: {reason}", member.guild)
        except Exception:
            await self.log_action(f"⚠️ Failed to kick {member.name}.", member.guild)
        
        await self._cleanup_messages_or_channel(member)


    async def _send_captcha(self, member: discord.Member):
        """Sends the CAPTCHA based on the configured mode."""
        guild_settings = await self.config.guild(member.guild).all()
        challenges = guild_settings["challenges"]
        kick_timeout = guild_settings["kick_timeout"]
        mode = guild_settings["verification_mode"]
        
        if not challenges:
            await self.log_action(f"⚠️ No challenges configured for {member.name}.", member.guild)
            return

        # 1. Prepare CAPTCHA data - Using LRU (Least Recently Used) for better challenge distribution
        
        challenge_items = list(challenges.items())
        
        # Sort the challenges by the 'last_used' timestamp (oldest first, default to 0 if not set)
        sorted_challenges = sorted(challenge_items, key=lambda item: item[1].get("last_used", 0))

        # Take the top N (e.g., 3) least recently used challenges
        top_n = 3
        least_used_pool = sorted_challenges[:top_n]
        
        # Select one random challenge from this smaller, weighted pool
        # If less than N challenges exist, it just chooses from the existing pool
        selected_challenge_id, challenge = random.choice(least_used_pool)
        
        # UPDATE CONFIG: Mark the selected challenge as used now
        async with self.config.guild(member.guild).challenges() as challenges_config:
            # Note: This updates the guild config when the challenge is chosen, not when it's completed.
            challenges_config[selected_challenge_id]["last_used"] = time.time()
        
        # Extract data from the selected challenge
        image_url = challenge["image_url"]
        options = challenge["options"]
        correct_option = challenge["correct_option"]
        
        # Prepare attempts for the embed
        attempts_data = self.active_captchas.get(member.id, {"attempts": 0})
        attempts_remaining = guild_settings['max_attempts'] - attempts_data['attempts']

        # Prepare View and Embed
        view = CaptchaView(self, member, correct_option, kick_timeout, options)
        captcha_embed = discord.Embed(
            title=f"Verification Test for {member.name}",
            description=(
                f"Please select the correct option based on the image below."
                f"\n\nYou have **{kick_timeout} seconds** to complete this attempt."
                f"\n**Attempts remaining:** **{attempts_remaining}**"
            ),
            color=await self.bot.get_embed_color(member)
        )
        if image_url: captcha_embed.set_image(url=image_url)


        # 2. Mode-Specific Delivery Logic
        
        # --- A. PUBLIC Mode (Visible, secured by interaction_check) ---
        if mode == "PUBLIC":
            channel_id = guild_settings["captcha_channel"]
            channel = member.guild.get_channel(channel_id)
            if not channel: return
            
            # Clean up old messages first (Handles retries)
            await self._delete_channel_messages(member.guild, channel, self.active_captchas[member.id])

            # Send Public Welcome Message
            welcome_embed = discord.Embed(
                title=guild_settings["welcome_embed_title"],
                description=guild_settings["welcome_embed_desc"],
                color=await self.bot.get_embed_color(member)
            )
            public_message = await channel.send(member.mention, embed=welcome_embed)
            
            # Send CAPTCHA as a secured reply
            captcha_message = await public_message.reply(
                f"**Verification Required:** {member.mention}", 
                embed=captcha_embed, 
                view=view,
                mention_author=False
            )
            view.message = captcha_message 
            self.active_captchas[member.id]["public_message_id"] = public_message.id 
            self.active_captchas[member.id]["message_id"] = captcha_message.id 

        # --- B. DM Mode (Private, requires DM notification/error handling) ---
        elif mode == "DM":
            channel_id = guild_settings["captcha_channel"]
            channel = member.guild.get_channel(channel_id)
            if not channel: 
                await self.log_action(f"⚠️ DM Mode selected but no captcha_channel is set for public notifications.", member.guild)
                return

            # Clear previous error/notification messages before retrying
            await self._delete_channel_messages(member.guild, channel, self.active_captchas[member.id])

            # --- ATTEMPT 1: Send CAPTCHA via DM ---
            try:
                # Send CAPTCHA message directly to the user's DM
                dm_message = await member.send(content="Please complete the CAPTCHA below!", embed=captcha_embed, view=view)
                view.message = dm_message 
                
                # --- Send Public Notification with DM Link ---
                notification_embed = discord.Embed(
                    title="✅ Check Your DMs!",
                    description=(
                        f"{member.mention}, please check your Direct Messages for the verification test."
                        f"\n\n[**Click here to jump to the DM CAPTCHA**]({dm_message.jump_url})"
                    ),
                    color=discord.Color.green()
                )
                # Send the notification in the public channel, delete after timeout
                notification_message = await channel.send(member.mention, embed=notification_embed, delete_after=kick_timeout)
                self.active_captchas[member.id]["public_message_id"] = notification_message.id # Track for cleanup

            # --- CATCH: DMs Disabled ---
            except discord.Forbidden:
                await self.log_action(f"❌ Failed to send CAPTCHA to DM for {member.name}. DMs are blocked.", member.guild)
                
                # Send public error message with retry button
                error_embed = discord.Embed(
                    title="🚫 DMs Disabled - Verification Failed",
                    description=(
                        f"{member.mention}, I cannot send you the CAPTCHA because your DMs are blocked."
                        f"\n\n**Please enable DMs for this server** and click the button below to retry."
                    ),
                    color=discord.Color.red()
                )
                # Create the Retry View
                retry_view = RetryView(self, member, kick_timeout)
                
                # Send the message with the retry button
                error_message = await channel.send(member.mention, embed=error_embed, view=retry_view)

                # Store the error message ID for cleanup 
                self.active_captchas[member.id]["error_message_id"] = error_message.id 
                
                # The kick timer continues to run, but the user is given the chance to retry by using the button.


        # --- C. PRIVATE_CHANNEL Mode (Truly private, requires channel creation/deletion) ---
        elif mode == "PRIVATE_CHANNEL":
            base_channel_id = guild_settings["captcha_channel"]
            base_channel = member.guild.get_channel(base_channel_id)
            category = base_channel.category if base_channel else None
            
            # Clean up old channel first (Handles retries)
            await self._delete_private_channel(member, self.active_captchas[member.id].get("channel_id"))
            
            # Create Overwrites (Deny everyone, allow user, allow bot)
            overwrites = {
                member.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                member.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
            }
            channel_name = f"verify-{member.name}".lower().replace(' ', '-')
            
            # Create Private Channel
            private_channel = await member.guild.create_text_channel(
                channel_name, category=category, overwrites=overwrites, reason="Captcha Verification"
            )
            self.active_captchas[member.id]["channel_id"] = private_channel.id
            
            # Send Public Welcome to guide user (THIS MESSAGE IS TRACKED FOR CLEANUP)
            public_message = await base_channel.send(member.mention, embed=discord.Embed(
                title=guild_settings["welcome_embed_title"],
                description=f"Welcome! Please head to {private_channel.mention} to complete verification.",
                color=await self.bot.get_embed_color(member)
            ), delete_after=kick_timeout)
            
            # Store the public message ID for cleanup
            self.active_captchas[member.id]["public_message_id"] = public_message.id 
            
            # Send CAPTCHA message to the private channel
            captcha_message = await private_channel.send(f"**Verification Test:** {member.mention}", embed=captcha_embed, view=view)
            view.message = captcha_message 


# ----------------------------------------------------------------
# --- Listeners & Background Task ---
# ----------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot: return
        guild_settings = await self.config.guild(member.guild).all()
        
        # Must have a channel set for any mode that uses it, and must have challenges
        if not guild_settings["challenges"] or (guild_settings["verification_mode"] != "DM" and not guild_settings["captcha_channel"]):
            return

        self.active_captchas[member.id] = {
            "start_time": time.time(),
            "attempts": 0,
            "message_id": None, 
            "public_message_id": None,
            "error_message_id": None, 
            "channel_id": None, 
        }
        
        await self.log_action(f"➡️ {member.name} joined. CAPTCHA initiated (Mode: {guild_settings['verification_mode']}).", member.guild)
        await self._send_captcha(member)

    @tasks.loop(seconds=60)
    async def kick_timed_out_users(self):
        """Background task to kick users who time out."""
        for user_id in list(self.active_captchas.keys()):
            data = self.active_captchas[user_id]
            start_time = data["start_time"]

            for guild in self.bot.guilds:
                member = guild.get_member(user_id)
                if member:
                    kick_timeout = await self.config.guild(guild).kick_timeout()
                    
                    if (time.time() - start_time) > kick_timeout:
                        await self._kick_user(member, f"Timed out waiting for CAPTCHA completion after {kick_timeout} seconds.")
                    break 

    @kick_timed_out_users.before_loop
    async def before_kick_timed_out_users(self):
        await self.bot.wait_until_ready()

# ----------------------------------------------------------------
# --- Configuration Commands ---
# ----------------------------------------------------------------

    def get_name_or_id(self, ctx: commands.Context, entity_id, entity_type: Literal["channel", "role"]):
        """Helper to safely retrieve entity name or ID for settings display."""
        if not entity_id: return "Not Set ❌"
        
        entity = None
        if entity_type == "channel":
            entity = ctx.guild.get_channel(entity_id)
        elif entity_type == "role": 
            entity = ctx.guild.get_role(entity_id)
            
        return f"{entity.mention} (`{entity_id}`)" if entity else f"ID: `{entity_id}` (Not Found)"

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def captchaset(self, ctx: commands.Context):
        """Manages the CaptchaGate settings for the server."""
        if ctx.invoked_subcommand is None:
            settings = await self.config.guild(ctx.guild).all()
            
            embed = discord.Embed(
                title="CaptchaGate Settings",
                color=await ctx.embed_color(),
            )
            embed.add_field(name="**Verification Mode**", value=f"`{settings['verification_mode']}`", inline=False)
            embed.add_field(name="Captcha Channel", value=self.get_name_or_id(ctx, settings["captcha_channel"], "channel"), inline=False)
            embed.add_field(name="Success Role", value=self.get_name_or_id(ctx, settings["success_role"], "role"), inline=False)
            embed.add_field(name="Log Channel", value=self.get_name_or_id(ctx, settings["log_channel"], "channel"), inline=False)
            embed.add_field(name="Kick Timeout", value=f"`{settings['kick_timeout']}` seconds", inline=True)
            embed.add_field(name="Max Attempts", value=f"`{settings['max_attempts']}` attempts", inline=True)
            embed.add_field(name="Total Challenges", value=f"`{len(settings['challenges'])}` configured", inline=False)
            embed.add_field(name="Welcome Title", value=f"`{settings['welcome_embed_title']}`", inline=False)
            embed.add_field(name="Welcome Description", value=f"`{settings['welcome_embed_desc'][:50]}...`", inline=False)

            await ctx.send(embed=embed)


    @captchaset.command(name="mode")
    async def captchaset_mode(self, ctx: commands.Context, mode: str):
        """
        Sets the delivery method for the CAPTCHA (case-insensitive).
        
        PUBLIC: Visible in a channel (secure only by interaction_check).
        DM: Private, sent via Direct Message (requires DMs to be open).
        PRIVATE_CHANNEL: Creates a private channel for verification (most secure, requires 'manage channels' perm).
        """
        mode = mode.upper()
        
        if mode not in ["PUBLIC", "DM", "PRIVATE_CHANNEL"]:
            valid_modes = ", ".join(["PUBLIC", "DM", "PRIVATE_CHANNEL"])
            return await ctx.send(f"❌ Invalid mode provided. Please choose one of: **{valid_modes}**.")
            
        if mode != "DM" and not await self.config.guild(ctx.guild).captcha_channel():
            return await ctx.send("❌ You must set a `captcha_channel` before using **PUBLIC** or **PRIVATE_CHANNEL** mode.")

        await self.config.guild(ctx.guild).verification_mode.set(mode)
        await ctx.send(f"✅ Verification mode set to **{mode}**.")
        
    @captchaset.command(name="channel")
    async def captchaset_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Sets the channel where the CAPTCHA will be displayed (or used as category for private mode)."""
        await self.config.guild(ctx.guild).captcha_channel.set(channel.id)
        await ctx.send(f"✅ CAPTCHA channel set to {channel.mention}.")

    @captchaset.command(name="role")
    async def captchaset_role(self, ctx: commands.Context, role: discord.Role):
        """Sets the role to be granted upon passing the CAPTCHA."""
        if role >= ctx.guild.me.top_role:
            return await ctx.send("❌ My highest role is below or equal to the role you are trying to set. I cannot assign it.")
            
        await self.config.guild(ctx.guild).success_role.set(role.id)
        await ctx.send(f"✅ Success role set to `{role.name}`.")

    @captchaset.command(name="logchannel")
    async def captchaset_logchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Sets the channel where CAPTCHA success/fail/kick logs will be outputted."""
        await self.config.guild(ctx.guild).log_channel.set(channel.id)
        await ctx.send(f"✅ Log channel set to {channel.mention}.")

    @captchaset.command(name="timeout")
    async def captchaset_timeout(self, ctx: commands.Context, seconds: int):
        """Sets the time limit (in seconds) before an unverified user is kicked."""
        if seconds < 60:
            return await ctx.send("❌ Timeout must be at least 60 seconds.")
            
        await self.config.guild(ctx.guild).kick_timeout.set(seconds)
        await ctx.send(f"✅ Kick timeout set to `{seconds}` seconds.")

    @captchaset.command(name="attempts")
    async def captchaset_attempts(self, ctx: commands.Context, attempts: int):
        """Sets the maximum number of times a user can fail the CAPTCHA before being kicked."""
        if attempts < 1:
            return await ctx.send("❌ Max attempts must be at least 1.")
            
        await self.config.guild(ctx.guild).max_attempts.set(attempts)
        await ctx.send(f"✅ Max attempts set to `{attempts}`.")

    @captchaset.command(name="welcometitle")
    async def captchaset_welcometitle(self, ctx: commands.Context, *, title: str):
        """Sets the title for the public welcome message embed. Use {user} for mention."""
        await self.config.guild(ctx.guild).welcome_embed_title.set(title)
        await ctx.send(f"✅ Public welcome message title set to: `{title}`")

    @captchaset.command(name="welcomedesc")
    async def captchaset_welcomedesc(self, ctx: commands.Context, *, description: str):
        """Sets the description for the public welcome message embed. Use {user} for mention."""
        await self.config.guild(ctx.guild).welcome_embed_desc.set(description)
        await ctx.send(f"✅ Public welcome message description set.")

    # ----------------------------------------------------------------
    # --- Challenge Management Commands ---
    # ----------------------------------------------------------------

    @captchaset.group(name="challenge")
    async def captchaset_challenge(self, ctx: commands.Context):
        """Manages the admin-provided CAPTCHA challenges."""
        pass 

    @captchaset_challenge.command(name="add")
    async def challenge_add(self, ctx: commands.Context, challenge_id: str, image_url: str, correct_option: str, *, options: str):
        """
        Adds a new image-based challenge.
        
        <challenge_id>: A unique ID (e.g., 'cat_1').
        <image_url>: A direct URL to the image to show.
        <correct_option>: The text of the correct option (must be one of the options).
        <options>: A comma-separated list of all possible option texts (e.g., "Cat, Dog, Bird").
        """
        options_list = [o.strip() for o in options.split(',')]
        
        if correct_option not in options_list:
            return await ctx.send("❌ The `correct_option` must be present in the list of `options`.")
            
        async with self.config.guild(ctx.guild).challenges() as challenges:
            if challenge_id in challenges:
                return await ctx.send(f"❌ A challenge with ID `{challenge_id}` already exists. Use `[p]captchaset challenge remove` first.")
                
            challenges[challenge_id] = {
                "image_url": image_url,
                "options": options_list,
                "correct_option": correct_option,
                # Store the current time when added. If it was never used, it's the "oldest" used one.
                "last_used": time.time(), 
            }

        await ctx.send(f"✅ Challenge `{challenge_id}` added! Correct option: `{correct_option}`. Options: {humanize_list(options_list)}")

    @captchaset_challenge.command(name="list")
    async def challenge_list(self, ctx: commands.Context):
        """Lists all currently configured challenges."""
        challenges = await self.config.guild(ctx.guild).challenges()
        if not challenges:
            return await ctx.send("No CAPTCHA challenges have been configured yet.")
            
        output = []
        for cid, c in challenges.items():
            # Display last used time in a readable format
            last_used_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(c.get("last_used", 0)))
            output.append(f"**{cid}**: Correct: `{c['correct_option']}` | Last Used: `{last_used_time}` | Options: {humanize_list(c['options'])}")
        
        pages = await self.bot.formatter.format_list_neatly(output)
        
        await menu(ctx, pages, DEFAULT_CONTROLS)

    @captchaset_challenge.command(name="remove")
    async def challenge_remove(self, ctx: commands.Context, challenge_id: str):
        """Removes an existing challenge by its ID."""
        async with self.config.guild(ctx.guild).challenges() as challenges:
            if challenge_id not in challenges:
                return await ctx.send(f"❌ Challenge ID `{challenge_id}` not found.")
            
            del challenges[challenge_id]
            
        await ctx.send(f"✅ Challenge `{challenge_id}` removed.")