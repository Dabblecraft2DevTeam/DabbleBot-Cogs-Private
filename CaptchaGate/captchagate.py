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

# *** Import only the Mixin class (no setup function needed) ***
from .lockdown import LockdownMixin 

# Define the possible verification modes
VERIFICATION_MODES = Literal["PUBLIC", "DM", "PRIVATE_CHANNEL"]

# --- View for the Captcha Options (Omitted for brevity, assumed unchanged) ---
class CaptchaView(discord.ui.View):
    # ... (code for CaptchaView remains the same) ...
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
            await interaction.response.send_message("✅ **Success!** You passed the CAPTCHA.", ephemeral=True)
            await self.cog.grant_role_and_cleanup(self.member) 
        else:
            await interaction.response.send_message("❌ **Incorrect!** Please try again.", ephemeral=True)
            await self.cog.handle_failed_attempt(self.member)
        await self.cog.log_action(f"CAPTCHA attempt by {self.member.name} ({self.member.id}). Correct: **{is_correct}**", self.member.guild)

# --- View for DM Retry (Omitted for brevity, assumed unchanged) ---
class RetryView(discord.ui.View):
    # ... (code for RetryView remains the same) ...
    def __init__(self, cog, member: discord.Member, timeout: int):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.member = member
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
        # Initialize the mixin before setting up shared resources
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
        
        # Tracks {member_id: {"start_time": float, "attempts": int, "message_id": int | ...}}
        self.active_captchas = {} 
        
        # Pass the main cog's functions/config to the mixin for access
        self.log_action = self.log_action 
        self._send_captcha = self._send_captcha

        self.kick_task = self.kick_timed_out_users.start()
        
        # *** NOTE: No call to setup_lockdown_commands needed here with the new Mixin structure. ***

    def cog_unload(self):
        self.kick_task.cancel()

# ----------------------------------------------------------------
# --- Utility Functions (All remain the same, bodies omitted for compactness) ---
# ----------------------------------------------------------------

    async def log_action(self, message: str, guild: Optional[discord.Guild] = None):
        # ... (Function body remains the same) ...
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
        # ... (Function body remains the same) ...
        message_keys = ["message_id", "public_message_id", "error_message_id"]
        for key in message_keys:
            message_id = member_data.get(key)
            if message_id:
                try:
                    member_data[key] = None 
                    message = await channel.fetch_message(message_id)
                    await message.delete()
                except Exception:
                    pass 

    async def _delete_private_channel(self, member: discord.Member, channel_id: Optional[int]):
        # ... (Function body remains the same) ...
        if not channel_id: return
        channel = member.guild.get_channel(channel_id)
        if channel:
            try:
                await channel.delete(reason="CaptchaGate verification complete/failed.")
            except Exception:
                await self.log_action(f"⚠️ Failed to delete private channel for {member.name}.", member.guild)
    
    async def _cleanup_messages_or_channel(self, member: discord.Member):
        # ... (Function body remains the same) ...
        guild_settings = await self.config.guild(member.guild).all()
        mode = guild_settings["verification_mode"]
        member_data = self.active_captchas.get(member.id)
        if not member_data: return
        if mode in ["PUBLIC", "DM", "PRIVATE_CHANNEL"]:
            channel_id = guild_settings["captcha_channel"]
            channel = member.guild.get_channel(channel_id)
            if channel: await self._delete_channel_messages(member.guild, channel, member_data)
        if mode == "PRIVATE_CHANNEL":
            await self._delete_private_channel(member, member_data.get("channel_id"))
        self.active_captchas.pop(member.id, None)


    async def grant_role_and_cleanup(self, member: discord.Member):
        # ... (Function body remains the same) ...
        guild_settings = await self.config.guild(member.guild).all()
        role_id = guild_settings["success_role"]
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
        # ... (Function body remains the same) ...
        if member.id not in self.active_captchas: return
        guild_settings = await self.config.guild(member.guild).all()
        max_attempts = guild_settings["max_attempts"]
        self.active_captchas[member.id]["attempts"] += 1
        current_attempts = self.active_captchas[member.id]["attempts"]
        if current_attempts >= max_attempts:
            await self._kick_user(member, f"Exceeded maximum CAPTCHA attempts ({max_attempts}).")
        else:
            remaining = max_attempts - current_attempts
            await self.log_action(f"❌ {member.name} failed attempt {current_attempts}/{max_attempts}. {remaining} left.", member.guild)
            await self._send_captcha(member)

    async def handle_dm_retry(self, member: discord.Member, error_message: discord.Message):
        # ... (Function body remains the same) ...
        member_data = self.active_captchas.get(member.id)
        if not member_data: return
        try: await error_message.delete()
        except Exception: pass
        member_data["error_message_id"] = None
        member_data["start_time"] = time.time() 
        await self._send_captcha(member)

    async def _kick_user(self, member: discord.Member, reason: str):
        # ... (Function body remains the same) ...
        try: await member.send(f"You have been kicked from **{member.guild.name}** because you failed to complete the CAPTCHA. Reason: `{reason}`")
        except discord.Forbidden: pass
        try:
            await member.kick(reason=f"CaptchaGate Kick: {reason}")
            await self.log_action(f"🔨 Kicked {member.name}. Reason: {reason}", member.guild)
        except Exception:
            await self.log_action(f"⚠️ Failed to kick {member.name}.", member.guild)
        await self._cleanup_messages_or_channel(member)


    async def _send_captcha(self, member: discord.Member):
        # ... (Function body remains the same) ...
        guild_settings = await self.config.guild(member.guild).all()
        challenges = guild_settings["challenges"]
        kick_timeout = guild_settings["kick_timeout"]
        mode = guild_settings["verification_mode"]
        if not challenges:
            await self.log_action(f"⚠️ No challenges configured for {member.name}.", member.guild)
            return

        # 1. Prepare CAPTCHA data - LRU selection (Omitted logic for brevity)
        challenge_items = list(challenges.items())
        sorted_challenges = sorted(challenge_items, key=lambda item: item[1].get("last_used", 0))
        top_n = 3
        least_used_pool = sorted_challenges[:top_n]
        selected_challenge_id, challenge = random.choice(least_used_pool)
        async with self.config.guild(member.guild).challenges() as challenges_config:
            challenges_config[selected_challenge_id]["last_used"] = time.time()
        image_url = challenge["image_url"]
        options = challenge["options"]
        correct_option = challenge["correct_option"]
        attempts_data = self.active_captchas.get(member.id, {"attempts": 0})
        attempts_remaining = guild_settings['max_attempts'] - attempts_data['attempts']

        # Prepare View and Embed (Omitted logic for brevity)
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


        # 2. Mode-Specific Delivery Logic (Omitted logic for brevity)
        
        # --- A. PUBLIC Mode ---
        if mode == "PUBLIC":
            channel_id = guild_settings["captcha_channel"]
            channel = member.guild.get_channel(channel_id)
            if not channel: return
            await self._delete_channel_messages(member.guild, channel, self.active_captchas[member.id])
            welcome_embed = discord.Embed(
                title=guild_settings["welcome_embed_title"],
                description=guild_settings["welcome_embed_desc"],
                color=await self.bot.get_embed_color(member)
            )
            public_message = await channel.send(member.mention, embed=welcome_embed)
            captcha_message = await public_message.reply(
                f"**Verification Required:** {member.mention}", embed=captcha_embed, view=view, mention_author=False
            )
            view.message = captcha_message 
            self.active_captchas[member.id]["public_message_id"] = public_message.id 
            self.active_captchas[member.id]["message_id"] = captcha_message.id 

        # --- B. DM Mode ---
        elif mode == "DM":
            channel_id = guild_settings["captcha_channel"]
            channel = member.guild.get_channel(channel_id)
            if not channel: 
                await self.log_action(f"⚠️ DM Mode selected but no captcha_channel is set for public notifications.", member.guild)
                return
            await self._delete_channel_messages(member.guild, channel, self.active_captchas[member.id])
            try:
                dm_message = await member.send(content="Please complete the CAPTCHA below!", embed=captcha_embed, view=view)
                view.message = dm_message 
                notification_embed = discord.Embed(
                    title="✅ Check Your DMs!",
                    description=f"{member.mention}, please check your Direct Messages for the verification test.\n\n[**Click here to jump to the DM CAPTCHA**]({dm_message.jump_url})",
                    color=discord.Color.green()
                )
                notification_message = await channel.send(member.mention, embed=notification_embed, delete_after=kick_timeout)
                self.active_captchas[member.id]["public_message_id"] = notification_message.id
            except discord.Forbidden:
                await self.log_action(f"❌ Failed to send CAPTCHA to DM for {member.name}. DMs are blocked.", member.guild)
                error_embed = discord.Embed(
                    title="🚫 DMs Disabled - Verification Failed",
                    description=f"{member.mention}, I cannot send you the CAPTCHA because your DMs are blocked.\n\n**Please enable DMs for this server** and click the button below to retry.",
                    color=discord.Color.red()
                )
                retry_view = RetryView(self, member, kick_timeout)
                error_message = await channel.send(member.mention, embed=error_embed, view=retry_view)
                self.active_captchas[member.id]["error_message_id"] = error_message.id 

        # --- C. PRIVATE_CHANNEL Mode ---
        elif mode == "PRIVATE_CHANNEL":
            base_channel_id = guild_settings["captcha_channel"]
            base_channel = member.guild.get_channel(base_channel_id)
            category = base_channel.category if base_channel else None
            await self._delete_private_channel(member, self.active_captchas[member.id].get("channel_id"))
            overwrites = {
                member.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                member.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
            }
            channel_name = f"verify-{member.name}".lower().replace(' ', '-')
            private_channel = await member.guild.create_text_channel(
                channel_name, category=category, overwrites=overwrites, reason="Captcha Verification"
            )
            self.active_captchas[member.id]["channel_id"] = private_channel.id
            public_message = await base_channel.send(member.mention, embed=discord.Embed(
                title=guild_settings["welcome_embed_title"],
                description=f"Welcome! Please head to {private_channel.mention} to complete verification.",
                color=await self.bot.get_embed_color(member)
            ), delete_after=kick_timeout)
            self.active_captchas[member.id]["public_message_id"] = public_message.id 
            captcha_message = await private_channel.send(f"**Verification Test:** {member.mention}", embed=captcha_embed, view=view)
            view.message = captcha_message 


# ----------------------------------------------------------------
# --- Listeners & Background Task ---
# ----------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot: return
        guild_settings = await self.config.guild(member.guild).all()
        
        if not guild_settings["challenges"] or (guild_settings["verification_mode"] != "DM" and not guild_settings["captcha_channel"]):
            return

        # *** Check for Lockdown (Logic inherited from LockdownMixin) ***
        if guild_settings["lockdown_enabled"]:
            async with self.config.guild(member.guild).lockdown_users() as locked_users:
                locked_users[str(member.id)] = time.time() 
            await self.log_action(f"⏸️ {member.name} joined during lockdown. User queued.", member.guild)
            return
        # *** End Lockdown Check ***

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
        # ... (Task body remains the same) ...
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
# --- Configuration Commands (Base and Settings) ---
# ----------------------------------------------------------------

    def get_name_or_id(self, ctx: commands.Context, entity_id, entity_type: Literal["channel", "role"]):
        # ... (Function body remains the same) ...
        if not entity_id: return "Not Set ❌"
        entity = None
        if entity_type == "channel": entity = ctx.guild.get_channel(entity_id)
        elif entity_type == "role": entity = ctx.guild.get_role(entity_id)
        return f"{entity.mention} (`{entity_id}`)" if entity else f"ID: `{entity_id}` (Not Found)"

    # *** PRIMARY COMMAND GROUP DEFINITION ***
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
        # ... (Function body remains the same) ...
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
        # ... (Function body remains the same) ...
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
                "last_used": time.time(), 
            }
        await ctx.send(f"✅ Challenge `{challenge_id}` added! Correct option: `{correct_option}`. Options: {humanize_list(options_list)}")

    @captchaset_challenge.command(name="list")
    async def challenge_list(self, ctx: commands.Context):
        # ... (Function body remains the same) ...
        challenges = await self.config.guild(ctx.guild).challenges()
        if not challenges:
            return await ctx.send("No CAPTCHA challenges have been configured yet.")
        output = []
        for cid, c in challenges.items():
            last_used_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(c.get("last_used", 0)))
            output.append(f"**{cid}**: Correct: `{c['correct_option']}` | Last Used: `{last_used_time}` | Options: {humanize_list(c['options'])}")
        pages = await self.bot.formatter.format_list_neatly(output)
        await menu(ctx, pages, DEFAULT_CONTROLS)

    @captchaset_challenge.command(name="remove")
    async def challenge_remove(self, ctx: commands.Context, challenge_id: str):
        # ... (Function body remains the same) ...
        async with self.config.guild(ctx.guild).challenges() as challenges:
            if challenge_id not in challenges:
                return await ctx.send(f"❌ Challenge ID `{challenge_id}` not found.")
            del challenges[challenge_id]
        await ctx.send(f"✅ Challenge `{challenge_id}` removed.")