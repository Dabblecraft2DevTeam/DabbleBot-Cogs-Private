import asyncio
import time 
import random
from typing import Literal, Optional

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import humanize_list
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu

# --- View for the Captcha Options ---

class CaptchaView(discord.ui.View):
    """
    A custom Discord View to handle the multiple-choice CAPTCHA buttons.
    """
    def __init__(self, cog, member: discord.Member, correct_answer: str, timeout: int, options: list[str]):
        # The timeout here controls the Discord View's internal listener
        super().__init__(timeout=timeout)
        self.cog = cog
        self.member = member
        self.correct_answer = correct_answer
        self.message: Optional[discord.Message] = None # To store the message this view is attached to
        
        # Dynamically create buttons based on the options provided
        self._create_buttons(options)

    def _create_buttons(self, options: list[str]):
        """Creates a button for each option and assigns the callback."""
        random.shuffle(options) # Shuffle options for security/variability
        
        for option in options:
            button = discord.ui.Button(label=option, style=discord.ButtonStyle.secondary)
            
            # Use a lambda or partial to pass the option label to the callback, 
            # ensuring the label is correctly captured for each button.
            button.callback = lambda interaction, label=option: self.process_answer(interaction, label)
            self.add_item(button)

    async def on_timeout(self):
        """Called when the view's internal timeout expires."""
        # Disable all buttons when the view times out
        self.stop()
        for item in self.children:
            item.disabled = True
        if self.message:
            await self.message.edit(view=self)
        # Note: The kick logic is primarily handled by the background task, 
        # which runs independently of this view's timeout.

    async def process_answer(self, interaction: discord.Interaction, user_answer: str):
        """Handles a button click and processes the user's answer."""
        if interaction.user != self.member:
            # Send an ephemeral message only the user can see
            await interaction.response.send_message("🚫 This CAPTCHA is not for you.", ephemeral=True)
            return

        # 1. Stop and disable the view
        self.stop()
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)
        
        # 2. Check the answer
        is_correct = user_answer == self.correct_answer

        if is_correct:
            await interaction.response.send_message("✅ **Success!** You passed the CAPTCHA.", ephemeral=True)
            # Use self.cog to call methods in the main Cog class
            await self.cog.grant_role_and_cleanup(self.member)
        else:
            await interaction.response.send_message("❌ **Incorrect!** Please try again.", ephemeral=True)
            await self.cog.handle_failed_attempt(self.member)
        
        await self.cog.log_action(f"CAPTCHA attempt by {self.member.name} ({self.member.id}). Correct: **{is_correct}**", self.member.guild)


# --- Main Cog Class ---

class CaptchaGate(commands.Cog):
    """
    A CAPTCHA system to verify new members and prevent bot entry.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        
        # Config setup: We use Config.guild() to store settings per server.
        self.config = Config.get_conf(self, identifier=14092025, force_registration=True)
        
        default_guild = {
            "captcha_channel": None,
            "success_role": None,
            "log_channel": None,
            "kick_timeout": 300,  # Time in seconds (5 minutes)
            "max_attempts": 3,
            "challenges": {},  # Stores the admin-provided CAPTCHA data
        }
        
        self.config.register_guild(**default_guild)
        
        # In-memory tracking for active users. Stores {member_id: {"start_time": float, "attempts": int}}
        self.active_captchas = {} 
        
        # Start the background task for checking timeouts
        self.kick_task = self.kick_timed_out_users.start()

    def cog_unload(self):
        """Clean up when the cog is unloaded."""
        # Ensure the background task is stopped when the cog is unloaded
        self.kick_task.cancel()

    # ----------------------------------------------------------------
    # --- Utility Functions ---
    # ----------------------------------------------------------------

    async def log_action(self, message: str, guild: Optional[discord.Guild] = None):
        """Helper function to output a log message to the configured channel."""
        if not guild:
            return
            
        channel_id = await self.config.guild(guild).log_channel()
        if channel_id:
            channel = guild.get_channel(channel_id)
            if channel:
                try:
                    await channel.send(f"**CAPTCHA LOG:** {message}")
                except discord.Forbidden:
                    # Ignore if we can't send to the log channel
                    pass 

    async def grant_role_and_cleanup(self, member: discord.Member):
        """Grants the success role and removes the user from tracking."""
        guild_settings = await self.config.guild(member.guild).all()
        role_id = guild_settings["success_role"]
        
        if role_id:
            role = member.guild.get_role(role_id)
            if role and role < member.guild.me.top_role:
                try:
                    await member.add_roles(role, reason="Passed CaptchaGate verification.")
                    await self.log_action(f"✅ {member.name} ({member.id}) passed the CAPTCHA and was granted role `{role.name}`.", member.guild)
                except discord.Forbidden:
                    await self.log_action(f"⚠️ Failed to grant role to {member.name}. Bot lacks permissions.", member.guild)
                except Exception as e:
                    await self.log_action(f"⚠️ Error granting role to {member.name}: {e}", member.guild)
            else:
                await self.log_action(f"⚠️ Success role not found or bot can't assign it (Bot role must be higher).", member.guild)

        self.active_captchas.pop(member.id, None)

    async def handle_failed_attempt(self, member: discord.Member):
        """Increments fail count and kicks user if max attempts are reached."""
        if member.id not in self.active_captchas:
            return
            
        guild_settings = await self.config.guild(member.guild).all()
        max_attempts = guild_settings["max_attempts"]
        self.active_captchas[member.id]["attempts"] += 1
        current_attempts = self.active_captchas[member.id]["attempts"]

        if current_attempts >= max_attempts:
            await self._kick_user(member, f"Exceeded maximum CAPTCHA attempts ({max_attempts}).")
        else:
            remaining = max_attempts - current_attempts
            await self.log_action(f"❌ {member.name} ({member.id}) failed attempt {current_attempts}/{max_attempts}. Remaining: {remaining}.", member.guild)
            # Re-send the captcha to the user
            await self._send_captcha(member)


    async def _kick_user(self, member: discord.Member, reason: str):
        """Internal function to handle kicking a user."""
        try:
            # Inform the user they are being kicked
            await member.send(f"You have been kicked from **{member.guild.name}** because you failed to complete the CAPTCHA. Reason: `{reason}`")
        except discord.Forbidden:
            pass # Can't DM the user
            
        try:
            # Perform the kick
            await member.kick(reason=f"CaptchaGate Kick: {reason}")
            await self.log_action(f"🔨 Kicked {member.name} ({member.id}). Reason: {reason}", member.guild)
        except discord.Forbidden:
            await self.log_action(f"⚠️ Failed to kick {member.name}. Bot lacks permissions.", member.guild)
        except Exception as e:
            await self.log_action(f"⚠️ Error kicking {member.name}: {e}", member.guild)

        self.active_captchas.pop(member.id, None)


    async def _send_captcha(self, member: discord.Member):
        """Sends a random CAPTCHA challenge to the user."""
        guild_settings = await self.config.guild(member.guild).all()
        challenges = guild_settings["challenges"]
        channel_id = guild_settings["captcha_channel"]
        kick_timeout = guild_settings["kick_timeout"]
        
        if not challenges or not channel_id:
            await self.log_action(f"⚠️ CAPTCHA cannot be sent to {member.name}. Challenges or channel not configured.", member.guild)
            return

        captcha_channel = member.guild.get_channel(channel_id)
        if not captcha_channel:
            await self.log_action(f"⚠️ Configured CAPTCHA channel not found.", member.guild)
            return

        # 1. Select a random challenge
        # We use random.choice on the list of values (the challenges themselves)
        challenge = random.choice(list(challenges.values()))
        
        image_url = challenge["image_url"]
        options = challenge["options"]
        correct_option = challenge["correct_option"]
        
        # Get attempts remaining for the embed
        attempts_data = self.active_captchas.get(member.id, {"attempts": 0})
        attempts_remaining = guild_settings['max_attempts'] - attempts_data['attempts']

        # 2. Create the View. The button creation and logic is handled inside the view class.
        view = CaptchaView(self, member, correct_option, kick_timeout, options)

        # 3. Create and send the embed
        embed = discord.Embed(
            title=f"Welcome to {member.guild.name}!",
            description=(
                f"To gain access, please select the correct option based on the image below."
                f"\n\nYou have **{kick_timeout} seconds** to complete this attempt."
                f"\n**Attempts remaining:** **{attempts_remaining}**"
            ),
            color=await self.bot.get_embed_color(member)
        )
        if image_url:
            embed.set_image(url=image_url)
        
        try:
            message = await captcha_channel.send(f"{member.mention} - Please complete the CAPTCHA!", embed=embed, view=view)
            view.message = message # Store the message reference in the view
        except discord.Forbidden:
            await self.log_action(f"⚠️ Failed to send CAPTCHA in configured channel. Bot lacks permissions.", member.guild)


    # ----------------------------------------------------------------
    # --- Listeners ---
    # ----------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Initiates the CAPTCHA process for new users."""
        if member.bot:
            return

        guild_settings = await self.config.guild(member.guild).all()
        
        if not guild_settings["captcha_channel"] or not guild_settings["challenges"]:
            return # Configuration not complete

        # 1. Start tracking the user
        self.active_captchas[member.id] = {
            "start_time": time.time(),
            "attempts": 0
        }
        
        await self.log_action(f"➡️ {member.name} ({member.id}) joined. CAPTCHA initiated.", member.guild)
        
        # 2. Send the CAPTCHA
        await self._send_captcha(member)

    # ----------------------------------------------------------------
    # --- Background Task (Timeout Kick) ---
    # ----------------------------------------------------------------

    @commands.loop(seconds=60) # Check every 60 seconds
    async def kick_timed_out_users(self):
        """Checks active CAPTCHAs and kicks users who have timed out."""
        # We need a copy of the keys because we will be modifying the dictionary during iteration
        for user_id in list(self.active_captchas.keys()):
            data = self.active_captchas[user_id]
            start_time = data["start_time"]

            # Iterate over all guilds the bot is in to find the member
            for guild in self.bot.guilds:
                member = guild.get_member(user_id)
                if member:
                    # Found the member, now check the timeout for *this* guild
                    kick_timeout = await self.config.guild(guild).kick_timeout()
                    
                    if (time.time() - start_time) > kick_timeout:
                        # User timed out!
                        await self._kick_user(member, f"Timed out waiting for CAPTCHA completion after {kick_timeout} seconds.")
                    
                    # Since the user was found in a guild, we break and move to the next user_id
                    break 

    @kick_timed_out_users.before_loop
    async def before_kick_timed_out_users(self):
        """Wait until the bot is ready before starting the task."""
        await self.bot.wait_until_ready()

    # ----------------------------------------------------------------
    # --- Configuration Commands ---
    # ----------------------------------------------------------------

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def captchaset(self, ctx: commands.Context):
        """Manages the CaptchaGate settings for the server."""
        if ctx.invoked_subcommand is None:
            settings = await self.config.guild(ctx.guild).all()
            
            # Helper function to get name or ID
            def get_name_or_id(entity_id, entity_type: Literal["channel", "role"]):
                if not entity_id:
                    return "Not Set ❌"
                if entity_type == "channel":
                    entity = ctx.guild.get_channel(entity_id)
                else: 
                    entity = ctx.guild.get_role(entity_id)
                    
                return f"{entity.mention} (`{entity_id}`)" if entity else f"ID: `{entity_id}` (Not Found)"

            embed = discord.Embed(
                title="CaptchaGate Settings",
                color=await ctx.embed_color(),
            )
            embed.add_field(name="Captcha Channel", value=get_name_or_id(settings["captcha_channel"], "channel"), inline=False)
            embed.add_field(name="Success Role", value=get_name_or_id(settings["success_role"], "role"), inline=False)
            embed.add_field(name="Log Channel", value=get_name_or_id(settings["log_channel"], "channel"), inline=False)
            embed.add_field(name="Kick Timeout", value=f"`{settings['kick_timeout']}` seconds", inline=True)
            embed.add_field(name="Max Attempts", value=f"`{settings['max_attempts']}` attempts", inline=True)
            embed.add_field(name="Total Challenges", value=f"`{len(settings['challenges'])}` configured", inline=False)

            await ctx.send(embed=embed)


    @captchaset.command(name="channel")
    async def captchaset_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Sets the channel where the CAPTCHA will be displayed."""
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
            }

        await ctx.send(f"✅ Challenge `{challenge_id}` added! Correct option: `{correct_option}`. Options: {humanize_list(options_list)}")

    @captchaset_challenge.command(name="list")
    async def challenge_list(self, ctx: commands.Context):
        """Lists all currently configured challenges."""
        challenges = await self.config.guild(ctx.guild).challenges()
        if not challenges:
            return await ctx.send("No CAPTCHA challenges have been configured yet.")
            
        output = [f"**{cid}**: Correct: `{c['correct_option']}` | Options: {humanize_list(c['options'])}" for cid, c in challenges.items()]
        
        # Use Red's built-in pagination if the list is long
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