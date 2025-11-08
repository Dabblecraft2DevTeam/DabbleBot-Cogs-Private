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

# --- View for the Captcha Options ---

class CaptchaView(discord.ui.View):
    """
    A custom Discord View to handle the multiple-choice CAPTCHA buttons.
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
            
            # Use lambda to correctly capture the current option for each button
            button.callback = lambda interaction, label=option: self.process_answer(interaction, label)
            self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """FIX: Only allows the target member to interact with the buttons."""
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
        await interaction.message.edit(view=self)
        
        is_correct = user_answer == self.correct_answer

        if is_correct:
            await interaction.response.send_message("✅ **Success!** You passed the CAPTCHA.", ephemeral=True)
            await self.cog.grant_role_and_cleanup(self.member, self.message) 
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
        }
        
        self.config.register_guild(**default_guild)
        
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

    async def grant_role_and_cleanup(self, member: discord.Member, message: Optional[discord.Message] = None):
        """Grants the success role and cleans up the CAPTCHA message."""
        guild_settings = await self.config.guild(member.guild).all()
        role_id = guild_settings["success_role"]
        
        # Role Granting Logic
        if role_id:
            role = member.guild.get_role(role_id)
            if role and role < member.guild.me.top_role:
                try:
                    await member.add_roles(role, reason="Passed CaptchaGate verification.")
                    await self.log_action(f"✅ {member.name} ({member.id}) passed the CAPTCHA and was granted role `{role.name}`.", member.guild)
                except Exception:
                    await self.log_action(f"⚠️ Error granting role to {member.name}.", member.guild)
            else:
                await self.log_action(f"⚠️ Success role not set or bot can't assign it.", member.guild)

        # Message Deletion (Cleanup)
        if message:
            try:
                await message.delete() 
            except Exception:
                pass
                
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
            await self._send_captcha(member)


    async def _kick_user(self, member: discord.Member, reason: str):
        """Internal function to handle kicking a user."""
        try:
            await member.send(f"You have been kicked from **{member.guild.name}** because you failed to complete the CAPTCHA. Reason: `{reason}`")
        except discord.Forbidden:
            pass
            
        try:
            await member.kick(reason=f"CaptchaGate Kick: {reason}")
            await self.log_action(f"🔨 Kicked {member.name} ({member.id}). Reason: {reason}", member.guild)
        except Exception:
            await self.log_action(f"⚠️ Failed to kick {member.name}. Bot lacks permissions.", member.guild)

        self.active_captchas.pop(member.id, None)


    async def _send_captcha(self, member: discord.Member):
        """Sends a public welcome embed and then the CAPTCHA as a secured reply."""
        guild_settings = await self.config.guild(member.guild).all()
        challenges = guild_settings["challenges"]
        channel_id = guild_settings["captcha_channel"]
        kick_timeout = guild_settings["kick_timeout"]
        
        if not challenges or not channel_id:
            return

        captcha_channel = member.guild.get_channel(channel_id)
        if not captcha_channel:
            return

        # --- FIX: Delete previous CAPTCHA reply message on retry ---
        member_data = self.active_captchas.get(member.id)
        if member_data and member_data.get("message_id"):
            try:
                old_message = await captcha_channel.fetch_message(member_data["message_id"])
                await old_message.delete()
            except Exception:
                pass 
        
        # 1. SEND PUBLIC WELCOME MESSAGE
        welcome_embed = discord.Embed(
            title=guild_settings["welcome_embed_title"].replace("{user}", member.name),
            description=guild_settings["welcome_embed_desc"].replace("{user}", member.name),
            color=await self.bot.get_embed_color(member)
        )
        
        try:
            public_message = await captcha_channel.send(member.mention, embed=welcome_embed)
        except discord.Forbidden:
            await self.log_action(f"⚠️ Failed to send public welcome message.", member.guild)
            return

        # 2. SELECT RANDOM CAPTCHA AND SEND SECURED REPLY
        
        challenge = random.choice(list(challenges.values()))
        image_url = challenge["image_url"]
        options = challenge["options"]
        correct_option = challenge["correct_option"]
        
        attempts_data = self.active_captchas.get(member.id, {"attempts": 0})
        attempts_remaining = guild_settings['max_attempts'] - attempts_data['attempts']

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
        if image_url:
            captcha_embed.set_image(url=image_url)

        try:
            captcha_message = await public_message.reply(
                f"**Verification Required:** {member.mention}", 
                embed=captcha_embed, 
                view=view,
                mention_author=False
            )
            
            view.message = captcha_message 
            self.active_captchas[member.id]["message_id"] = captcha_message.id 
            
        except discord.Forbidden:
            await self.log_action(f"⚠️ Failed to send CAPTCHA reply. Bot lacks permissions.", member.guild)


# ----------------------------------------------------------------
# --- Listeners & Background Task ---
# ----------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # ... (listener logic remains the same)
        if member.bot: return

        guild_settings = await self.config.guild(member.guild).all()
        if not guild_settings["captcha_channel"] or not guild_settings["challenges"]:
            return

        self.active_captchas[member.id] = {
            "start_time": time.time(),
            "attempts": 0,
            "message_id": None
        }
        
        await self.log_action(f"➡️ {member.name} ({member.id}) joined. CAPTCHA initiated.", member.guild)
        await self._send_captcha(member)

    @tasks.loop(seconds=60)
    async def kick_timed_out_users(self):
        # ... (task logic remains the same)
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
# --- Configuration Commands (RESTORED) ---
# ----------------------------------------------------------------

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def captchaset(self, ctx: commands.Context):
        """Manages the CaptchaGate settings for the server."""
        if ctx.invoked_subcommand is None:
            settings = await self.config.guild(ctx.guild).all()
            
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
            embed.add_field(name="Welcome Title", value=f"`{settings['welcome_embed_title']}`", inline=False)
            embed.add_field(name="Welcome Description", value=f"`{settings['welcome_embed_desc'][:50]}...`", inline=False)

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
    # --- Challenge Management Commands (RESTORED) ---
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