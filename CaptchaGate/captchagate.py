import asyncio
import time 
import random
from typing import Literal, Optional

import discord
from discord.ext import tasks 
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import humanize_list, box
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu

from .lockdown import LockdownMixin 

# Define the possible verification modes
VERIFICATION_MODES = Literal["PUBLIC", "DM", "PRIVATE_CHANNEL"]

# --- View Classes (Necessary for command functionality) ---

class CaptchaView(discord.ui.View):
    """View for handling button-based CAPTCHA answers."""
    def __init__(self, cog, member: discord.Member, correct_answer: str, timeout: int, options: list[str]):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.member = member
        self.correct_answer = correct_answer
        self.message: Optional[discord.Message] = None 
        self._create_buttons(options)

    def _create_buttons(self, options: list[str]):
        random.shuffle(options)
        for option in options:
            button = discord.ui.Button(label=option, style=discord.ButtonStyle.secondary)
            button.callback = lambda interaction, label=option: self.process_answer(interaction, label)
            self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user == self.member:
            return True
        await interaction.response.send_message("🚫 This CAPTCHA is not for you. You must wait for your own verification.", ephemeral=True)
        return False

    async def on_timeout(self):
        self.stop()
        for item in self.children: item.disabled = True
        if self.message: await self.message.edit(view=self)

    async def process_answer(self, interaction: discord.Interaction, user_answer: str):
        self.stop()
        for item in self.children: item.disabled = True
        if interaction.message: await interaction.message.edit(view=self)
        is_correct = user_answer == self.correct_answer
        
        if is_correct:
            await interaction.response.send_message("✅ **Success!** You passed the CAPTCHA. Assigning role now.", ephemeral=True)
            await self.cog.grant_role_and_cleanup(self.member) 
        else:
            await interaction.response.send_message("❌ **Incorrect!** Please try again.", ephemeral=True)
            await self.cog.handle_failed_attempt(self.member)
        await self.cog.log_action(f"CAPTCHA attempt by {self.member.name} ({self.member.id}). Correct: **{is_correct}**", self.member.guild)

class RetryView(discord.ui.View):
    """View for allowing users to retry CAPTCHA if DMs were initially disabled."""
    def __init__(self, cog, member: discord.Member, timeout: int):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.member = member
        self.message: Optional[discord.Message] = None
        self.add_item(discord.ui.Button(label="I've Enabled DMs - Retry CAPTCHA", style=discord.ButtonStyle.success))
        self.children[0].callback = self.on_retry_click

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user == self.member:
            return True
        await interaction.response.send_message("🚫 This button is only for the person who needs to verify.", ephemeral=True)
        return False

    async def on_retry_click(self, interaction: discord.Interaction):
        self.stop()
        for item in self.children: item.disabled = True
        await interaction.message.edit(view=self)
        await interaction.response.send_message("Attempting to resend CAPTCHA via DM...", ephemeral=True)
        await self.cog.log_action(f"🔄 {self.member.name} requested CAPTCHA retry.", self.member.guild)
        await self.cog.handle_dm_retry(self.member, interaction.message)

    async def on_timeout(self):
        for item in self.children: item.disabled = True
        if self.message: await self.message.edit(view=self)


# --- Main Cog Class ---

class CaptchaGate(LockdownMixin, commands.Cog): 
    """
    A CAPTCHA system to verify new members with multiple delivery options, 
    including a raid/lockdown mode.
    """

    def __init__(self, bot: Red):
        # Call the __init__ of all base classes, including LockdownMixin
        super().__init__() 
        
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
            # --- Lockdown Feature Settings ---
            "lockdown_enabled": False, 
            "lockdown_users": {},      
            "lockdown_message_id": None, 
        }
        
        self.config.register_guild(**default_guild)
        
        self.active_captchas = {} 
        
        # Ensure the Mixin methods can access necessary attributes
        self.log_action = self.log_action 
        self._send_captcha = self._send_captcha

        self.kick_task = self.kick_timed_out_users.start()
        
        # --- FIX FOR MIXIN COMMAND CONFLICT ---
        # This attaches the 'lockdown' command (a method in the Mixin) to the 
        # 'captchaset' Group defined in this class, preventing command conflicts.
        self._add_lockdown_command(self.captchaset) 
        # ------------------------------------


    def cog_unload(self):
        self.kick_task.cancel()

# ----------------------------------------------------------------
# --- Utility Functions ---
# ----------------------------------------------------------------

    async def log_action(self, message: str, guild: Optional[discord.Guild] = None):
        """Logs an action to the configured log channel."""
        if not guild: return
        channel_id = await self.config.guild(guild).log_channel()
        if channel_id:
            channel = guild.get_channel(channel_id)
            if channel:
                try:
                    await channel.send(f"**CAPTCHA LOG:** {message}")
                except discord.Forbidden:
                    pass 

    def get_name_or_id(self, ctx: commands.Context, entity_id, entity_type: Literal["channel", "role"]):
        """Utility to get a channel/role name or return the ID."""
        if not entity_id:
            return "`Not Set`"
        
        if entity_type == "channel":
            entity = ctx.guild.get_channel(entity_id)
        elif entity_type == "role":
            entity = ctx.guild.get_role(entity_id)
        
        if entity:
            return entity.mention
        return f"**ID:** `{entity_id}` (Not found)"

    async def _delete_channel_messages(self, guild: discord.Guild, channel: discord.TextChannel, member_data: dict):
        """Deletes bot-sent messages in a public channel."""
        message_ids = [member_data.get("message_id"), member_data.get("public_message_id")]
        for msg_id in message_ids:
            if msg_id:
                try:
                    message = await channel.fetch_message(msg_id)
                    await message.delete()
                except discord.NotFound:
                    pass
                except discord.Forbidden:
                    await self.log_action(f"Failed to delete message {msg_id} in {channel.name} due to permissions.", guild)

    async def _delete_private_channel(self, member: discord.Member, channel_id: Optional[int]):
        """Deletes a private channel associated with the CAPTCHA."""
        if channel_id:
            channel = member.guild.get_channel(channel_id)
            if channel:
                try:
                    await channel.delete()
                except discord.Forbidden:
                    await self.log_action(f"Failed to delete private channel {channel.name} for {member.name} due to permissions.", member.guild)
                except discord.HTTPException:
                    pass

    async def _cleanup_messages_or_channel(self, member: discord.Member):
        """Cleans up messages (Public/DM mode) or the private channel (Private mode)."""
        guild = member.guild
        member_data = self.active_captchas.get(member.id)
        if not member_data:
            return

        settings = await self.config.guild(guild).all()
        mode = settings["verification_mode"]
        
        if mode == "PUBLIC":
            if settings["captcha_channel"]:
                channel = guild.get_channel(settings["captcha_channel"])
                if channel and isinstance(channel, discord.TextChannel):
                    await self._delete_channel_messages(guild, channel, member_data)

        elif mode == "PRIVATE_CHANNEL":
            await self._delete_private_channel(member, member_data.get("channel_id"))
            
        # Clean up active CAPTCHA state
        if member.id in self.active_captchas:
            del self.active_captchas[member.id]


    async def grant_role_and_cleanup(self, member: discord.Member):
        """Grants the success role and cleans up the verification state."""
        guild = member.guild
        role_id = await self.config.guild(guild).success_role()
        
        if role_id:
            role = guild.get_role(role_id)
            if role:
                try:
                    await member.add_roles(role, reason="Passed CAPTCHA verification.")
                    await self.log_action(f"✅ Granted role **{role.name}** to {member.name}.", guild)
                except discord.Forbidden:
                    await self.log_action(f"❌ Failed to grant role **{role.name}** to {member.name}. Check bot permissions.", guild)
            else:
                await self.log_action("Success role is configured but not found in the server.", guild)

        await self._cleanup_messages_or_channel(member)


    async def handle_failed_attempt(self, member: discord.Member):
        """Handles a failed CAPTCHA attempt."""
        guild = member.guild
        settings = await self.config.guild(guild).all()
        max_attempts = settings["max_attempts"]
        
        if member.id not in self.active_captchas:
            return 
            
        self.active_captchas[member.id]["attempts"] += 1
        current_attempts = self.active_captchas[member.id]["attempts"]
        
        if current_attempts >= max_attempts:
            await self._kick_user(member, f"Failed CAPTCHA verification after {max_attempts} attempts.")
            await self._cleanup_messages_or_channel(member) 
            await self.log_action(f"💀 Kicked {member.name} for failing CAPTCHA after {current_attempts} attempts.", guild)
        else:
            await self.log_action(f"⚠️ {member.name} failed attempt {current_attempts}/{max_attempts}.", guild)
            # Resend CAPTCHA immediately
            await self._send_captcha(member)


    async def handle_dm_retry(self, member: discord.Member, error_message: discord.Message):
        """Handles a user retrying DM CAPTCHA after enabling DMs."""
        await error_message.delete()
        self.active_captchas[member.id]["error_message_id"] = None
        await self._send_captcha(member)


    async def _kick_user(self, member: discord.Member, reason: str):
        """Attempts to kick a member."""
        try:
            await member.kick(reason=reason)
            await self.log_action(f"Kicked {member.name}. Reason: {reason}", member.guild)
        except discord.Forbidden:
            await self.log_action(f"❌ Failed to kick {member.name} (Forbidden). Check bot permissions.", member.guild)
        except discord.HTTPException as e:
            await self.log_action(f"❌ Failed to kick {member.name} (HTTPException: {e}).", member.guild)


    async def _send_captcha(self, member: discord.Member):
        """Sends the CAPTCHA challenge based on the configured mode."""
        guild = member.guild
        settings = await self.config.guild(guild).all()
        mode = settings["verification_mode"]
        challenges = settings["challenges"]

        if not challenges:
            await self.log_action("❌ Cannot send CAPTCHA: No challenges configured.", guild)
            return

        challenge_id, challenge_data = random.choice(list(challenges.items()))
        correct_answer = challenge_data["correct_option"]
        options = challenge_data["options"]
        image_url = challenge_data["image_url"]

        embed = discord.Embed(
            title=settings["welcome_embed_title"],
            description=settings["welcome_embed_desc"],
            color=discord.Color.blue()
        )
        embed.set_image(url=image_url)
        embed.add_field(name="Instructions", value="Select the correct option below to verify.", inline=False)
        
        captcha_view = CaptchaView(self, member, correct_answer, settings["kick_timeout"], options)

        # 1. Private Channel Mode
        if mode == "PRIVATE_CHANNEL":
            channel_id = self.active_captchas[member.id].get("channel_id")
            if not channel_id:
                try:
                    # Create private channel logic (simplified placeholder)
                    overwrites = {
                        guild.default_role: discord.PermissionOverwrite(read_messages=False),
                        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                        member: discord.PermissionOverwrite(read_messages=True, send_messages=False)
                    }
                    channel = await guild.create_text_channel(
                        f"verify-{member.name}", 
                        overwrites=overwrites,
                        reason="CAPTCHA verification channel."
                    )
                    self.active_captchas[member.id]["channel_id"] = channel.id
                except discord.Forbidden:
                    return await self.log_action("❌ Failed to create private channel (Forbidden). Check bot permissions.", guild)
            else:
                channel = guild.get_channel(channel_id)
            
            try:
                msg = await channel.send(member.mention, embed=embed, view=captcha_view)
                captcha_view.message = msg
                self.active_captchas[member.id]["message_id"] = msg.id
            except discord.Forbidden:
                await self.log_action("❌ Failed to send CAPTCHA in private channel (Forbidden).", guild)

        # 2. DM Mode
        elif mode == "DM":
            try:
                msg = await member.send(embed=embed, view=captcha_view)
                captcha_view.message = msg
                self.active_captchas[member.id]["message_id"] = msg.id
                
            except discord.Forbidden:
                # DMs are disabled, send public error message
                error_channel_id = settings["captcha_channel"]
                if error_channel_id:
                    error_channel = guild.get_channel(error_channel_id)
                    if error_channel:
                        error_embed = discord.Embed(
                            title="⚠️ DM Verification Failed",
                            description=f"{member.mention}, I cannot DM you. Please enable DMs for this server and click the button below to retry.",
                            color=discord.Color.orange()
                        )
                        retry_view = RetryView(self, member, settings["kick_timeout"])
                        try:
                            error_msg = await error_channel.send(embed=error_embed, view=retry_view)
                            retry_view.message = error_msg
                            self.active_captchas[member.id]["error_message_id"] = error_msg.id
                            
                        except discord.Forbidden:
                            await self.log_action("❌ Failed to send DM-disabled message in captcha channel (Forbidden).", guild)

        # 3. Public Channel Mode
        elif mode == "PUBLIC":
            channel_id = settings["captcha_channel"]
            if channel_id:
                channel = guild.get_channel(channel_id)
                if channel:
                    try:
                        msg = await channel.send(member.mention, embed=embed, view=captcha_view)
                        captcha_view.message = msg
                        self.active_captchas[member.id]["message_id"] = msg.id
                    except discord.Forbidden:
                        await self.log_action("❌ Failed to send CAPTCHA in public channel (Forbidden).", guild)


# ----------------------------------------------------------------
# --- Listeners & Background Task ---
# ----------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot: return
        guild_settings = await self.config.guild(member.guild).all()
        
        # Check if CAPTCHA is even configured
        if not guild_settings["challenges"] or (guild_settings["verification_mode"] != "DM" and not guild_settings["captcha_channel"]):
            return

        # Handle Lockdown Queue
        if guild_settings["lockdown_enabled"]:
            async with self.config.guild(member.guild).lockdown_users() as locked_users:
                locked_users[str(member.id)] = time.time() 
            await self.log_action(f"⏸️ {member.name} joined during lockdown. User queued.", member.guild)
            return

        # Start CAPTCHA
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
        """Kicks users who have timed out on the CAPTCHA."""
        for guild_id in await self.config.all_guilds():
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue

            settings = await self.config.guild(guild).all()
            timeout = settings["kick_timeout"]
            
            # Check users in active CAPTCHA
            expired_users = []
            current_time = time.time()
            for user_id, data in list(self.active_captchas.items()):
                if guild.get_member(user_id) and current_time - data["start_time"] > timeout:
                    expired_users.append(user_id)

            for user_id in expired_users:
                member = guild.get_member(user_id)
                if member:
                    await self._kick_user(member, f"CAPTCHA verification timed out after {timeout} seconds.")
                    await self._cleanup_messages_or_channel(member)
                    
            # Check users in Lockdown Queue (optional: remove long-expired users from queue)
            # This logic is typically simpler: only kick on CAPTCHA fail/timeout.
            # We don't kick users just for being in the lockdown queue, as that's a bot-imposed wait.


    @kick_timed_out_users.before_loop
    async def before_kick_timed_out_users(self):
        await self.bot.wait_until_ready()

# ----------------------------------------------------------------
# --- Configuration Commands (The only place the group is defined) ---
# ----------------------------------------------------------------

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def captchaset(self, ctx: commands.Context):
        """Manages the CaptchaGate settings for the server."""
        if ctx.invoked_subcommand is None:
            settings = await self.config.guild(ctx.guild).all()
            embed = discord.Embed(title="CaptchaGate Settings", color=await ctx.embed_color())
            embed.add_field(name="**Verification Mode**", value=f"`{settings['verification_mode']}`", inline=False)
            embed.add_field(name="Captcha Channel", value=self.get_name_or_id(ctx, settings["captcha_channel"], "channel"), inline=False)
            embed.add_field(name="Success Role", value=self.get_name_or_id(ctx, settings["success_role"], "role"), inline=False)
            embed.add_field(name="Log Channel", value=self.get_name_or_id(ctx, settings["log_channel"], "channel"), inline=False)
            embed.add_field(name="Kick Timeout", value=f"`{settings['kick_timeout']}` seconds", inline=True)
            embed.add_field(name="Max Attempts", value=f"`{settings['max_attempts']}` attempts", inline=True)
            embed.add_field(name="Total Challenges", value=f"`{len(settings['challenges'])}` configured", inline=False)
            embed.add_field(name="Welcome Title", value=f"`{settings['welcome_embed_title']}`", inline=False)
            embed.add_field(name="Welcome Description", value=f"`{settings['welcome_embed_desc'][:50]}...`", inline=False)
            embed.add_field(name="Lockdown Enabled", value=f"**{'Yes' if settings['lockdown_enabled'] else 'No'}**", inline=True)
            embed.add_field(name="Queued Users", value=f"`{len(settings['lockdown_users'])}`", inline=True)
            await ctx.send(embed=embed)


    @captchaset.command(name="mode")
    async def captchaset_mode(self, ctx: commands.Context, mode: str):
        """
        Sets the CAPTCHA delivery mode.
        
        Options: `PUBLIC`, `DM`, `PRIVATE_CHANNEL`
        """
        mode = mode.upper()
        if mode not in VERIFICATION_MODES.__args__:
            return await ctx.send(f"❌ Invalid mode. Must be one of: {humanize_list(VERIFICATION_MODES.__args__)}")
        
        await self.config.guild(ctx.guild).verification_mode.set(mode)
        await ctx.send(f"✅ Verification mode set to **{mode}**.")
        
    @captchaset.command(name="channel")
    async def captchaset_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Sets the channel where CAPTCHAs or error messages will be sent."""
        await self.config.guild(ctx.guild).captcha_channel.set(channel.id)
        await ctx.send(f"✅ CAPTCHA channel set to {channel.mention}.")
        
    @captchaset.command(name="role")
    async def captchaset_role(self, ctx: commands.Context, role: discord.Role):
        """Sets the role granted to users who successfully pass the CAPTCHA."""
        if role >= ctx.guild.me.top_role:
            return await ctx.send("❌ The success role must be lower than the bot's top role.")
        await self.config.guild(ctx.guild).success_role.set(role.id)
        await ctx.send(f"✅ Success role set to **{role.name}**.")

    @captchaset.command(name="logchannel")
    async def captchaset_logchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Sets the channel where bot actions (kicks, fails, successes) will be logged."""
        await self.config.guild(ctx.guild).log_channel.set(channel.id)
        await ctx.send(f"✅ Log channel set to {channel.mention}.")

    @captchaset.command(name="timeout")
    async def captchaset_timeout(self, ctx: commands.Context, seconds: int):
        """Sets the time (in seconds) a user has to complete the CAPTCHA before being kicked."""
        if seconds < 60 or seconds > 3600:
            return await ctx.send("❌ Timeout must be between 60 and 3600 seconds (1 hour).")
        await self.config.guild(ctx.guild).kick_timeout.set(seconds)
        await ctx.send(f"✅ Kick timeout set to **{seconds}** seconds.")

    @captchaset.command(name="attempts")
    async def captchaset_attempts(self, ctx: commands.Context, count: int):
        """Sets the maximum number of times a user can attempt the CAPTCHA before being kicked."""
        if count < 1 or count > 5:
            return await ctx.send("❌ Max attempts must be between 1 and 5.")
        await self.config.guild(ctx.guild).max_attempts.set(count)
        await ctx.send(f"✅ Max attempts set to **{count}**.")
        
    @captchaset.command(name="title")
    async def captchaset_title(self, ctx: commands.Context, *, title: str):
        """Sets the title for the CAPTCHA embed."""
        if len(title) > 256: return await ctx.send("❌ Title cannot exceed 256 characters.")
        await self.config.guild(ctx.guild).welcome_embed_title.set(title)
        await ctx.send("✅ Welcome embed title updated.")
        
    @captchaset.command(name="description", aliases=["desc"])
    async def captchaset_description(self, ctx: commands.Context, *, description: str):
        """Sets the description/instruction text for the CAPTCHA embed."""
        if len(description) > 2048: return await ctx.send("❌ Description cannot exceed 2048 characters.")
        await self.config.guild(ctx.guild).welcome_embed_desc.set(description)
        await ctx.send("✅ Welcome embed description updated.")


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
        Adds a new CAPTCHA challenge.
        
        <challenge_id>: A unique ID (e.g., 'car_test1').
        <image_url>: Direct link to the image (must be hosted online).
        <correct_option>: The label for the correct button (e.g., 'Car').
        <options>: A comma-separated list of all button labels (e.g., 'Car, Truck, Bike, Boat').
        """
        if challenge_id in await self.config.guild(ctx.guild).challenges():
            return await ctx.send("❌ A challenge with that ID already exists.")

        all_options = [opt.strip() for opt in options.split(',')]
        if correct_option not in all_options:
            return await ctx.send("❌ The `correct_option` must be included in the comma-separated `options` list.")
        if len(all_options) < 2:
            return await ctx.send("❌ You must provide at least two options.")
            
        new_challenge = {
            "image_url": image_url,
            "correct_option": correct_option,
            "options": all_options
        }
        
        async with self.config.guild(ctx.guild).challenges() as challenges:
            challenges[challenge_id] = new_challenge
            
        await ctx.send(f"✅ CAPTCHA challenge **{challenge_id}** added. Total challenges: {len(challenges)}")

    @captchaset_challenge.command(name="list")
    async def challenge_list(self, ctx: commands.Context):
        """Lists all configured CAPTCHA challenges."""
        challenges = await self.config.guild(ctx.guild).challenges()
        if not challenges:
            return await ctx.send("No CAPTCHA challenges have been configured.")
            
        pages = []
        for challenge_id, data in challenges.items():
            content = (
                f"**ID:** `{challenge_id}`\n"
                f"**Correct:** `{data['correct_option']}`\n"
                f"**Options:** {humanize_list([box(o) for o in data['options']])}\n"
                f"**Image URL:** <{data['image_url']}>"
            )
            pages.append(content)
            
        await menu(ctx, pages, DEFAULT_CONTROLS)

    @captchaset_challenge.command(name="remove")
    async def challenge_remove(self, ctx: commands.Context, challenge_id: str):
        """Removes an existing CAPTCHA challenge by its ID."""
        async with self.config.guild(ctx.guild).challenges() as challenges:
            if challenge_id not in challenges:
                return await ctx.send("❌ No challenge found with that ID.")
            
            del challenges[challenge_id]
            
        await ctx.send(f"✅ CAPTCHA challenge **{challenge_id}** removed. Total challenges: {len(challenges)}")