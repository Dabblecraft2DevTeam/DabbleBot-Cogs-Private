"""HCLinkApprovals Red-DiscordBot Cog for HC Account Migration Staff Reviews."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Set, Union

import discord
from discord.ext import tasks
from redbot.core import Config, commands
from redbot.core.bot import Red

from .client import HCLinkClient, HCLinkClientError
from .embeds import build_migration_embed, mark_embed_resolved
from .views import ApprovalView

logger = logging.getLogger("red.dabblebot.hclinkapprovals")


class HCLinkApprovals(commands.Cog):
    """
    Staff review cog for the Naval Battlezone HC Account Migration system.
    Surfaces pending manual review requests to staff, renders advisory verification data,
    and handles Approve/Reject button interactions with HMAC-authenticated backend API calls.
    """

    CONFIG_IDENTIFIER = 8446944301

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(
            self,
            identifier=self.CONFIG_IDENTIFIER,
            force_registration=True,
        )

        default_global: Dict[str, Any] = {
            "backend_url": "https://example.com",
            "shared_secret": "",
            "channel_id": None,
            "staff_role_id": None,
            "poll_interval": 30,
            "posted_sessions": {},
        }
        self.config.register_global(**default_global)

        self.client = HCLinkClient()
        self._init_task: Optional[asyncio.Task] = None
        self._poller_started = False

    async def cog_load(self) -> None:
        """Called by Red when the cog is loaded."""
        self._init_task = asyncio.create_task(self._initialize_cog())

    async def _initialize_cog(self) -> None:
        """Initialize backend client config, re-register persistent views, and start poller."""
        await self.bot.wait_until_red_ready()

        backend_url = await self.config.backend_url()
        secret = await self.config.shared_secret()
        poll_interval = await self.config.poll_interval()

        self.client.update_config(base_url=backend_url, shared_secret=secret)

        # Restore persistent views for any still-pending sessions
        posted_sessions = await self.config.posted_sessions()
        restored_count = 0
        for token, meta in posted_sessions.items():
            if isinstance(meta, dict) and meta.get("status") == "pending":
                try:
                    self.bot.add_view(ApprovalView(self, token))
                    restored_count += 1
                except Exception as exc:
                    logger.debug("Could not restore view for session %s: %s", token, exc)

        if restored_count:
            logger.info("Restored %d persistent approval views.", restored_count)

        # Start the background polling task
        if not self.poller_task.is_running():
            self.poller_task.change_interval(seconds=max(poll_interval, 5))
            self.poller_task.start()
            self._poller_started = True

    def cog_unload(self) -> None:
        """Called by Red when cog is unloaded."""
        if self.poller_task.is_running():
            self.poller_task.cancel()
        if self._init_task and not self._init_task.done():
            self._init_task.cancel()
        asyncio.create_task(self.client.close())

    async def update_session_status(
        self,
        token: str,
        new_status: str,
        user_id: Optional[Union[int, str]] = None,
    ) -> None:
        """Update session status in Config mapping store."""
        posted = await self.config.posted_sessions()
        if token in posted and isinstance(posted[token], dict):
            posted[token]["status"] = new_status
            if user_id:
                posted[token]["handled_by"] = str(user_id)
            posted[token]["updated_at"] = time.time()
            await self.config.posted_sessions.set(posted)

    # -------------------------------------------------------------------------
    # Background Poller Task
    # -------------------------------------------------------------------------

    @tasks.loop(seconds=30)
    async def poller_task(self) -> None:
        """
        Background loop to query pending migration sessions from backend
        and synchronize Discord embeds.
        """
        try:
            await self._poll_pending_sessions()
        except Exception as exc:
            logger.warning("Error in HCLink pending reviews poller: %s", exc)

    @poller_task.before_loop
    async def before_poller_task(self) -> None:
        """Ensure Red is completely ready before polling starts."""
        await self.bot.wait_until_red_ready()

    async def _poll_pending_sessions(self) -> None:
        """Execute one polling cycle against the migration backend."""
        channel_id = await self.config.channel_id()
        secret = await self.config.shared_secret()

        if not channel_id or not secret:
            return

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.debug("Staff channel %s not accessible or not found.", channel_id)
                return

        if not isinstance(channel, (discord.abc.Messageable, discord.TextChannel, discord.Thread)):
            return

        # Fetch pending list from backend
        try:
            pending_list = await self.client.get_pending_reviews()
        except HCLinkClientError as exc:
            logger.warning("Failed to fetch pending sessions from backend: %s", exc)
            return

        posted_sessions = await self.config.posted_sessions()
        dirty = False
        active_tokens: Set[str] = set()

        # 1. Post embeds for new pending sessions
        for session in pending_list:
            token = session.get("session_token")
            if not token:
                continue

            active_tokens.add(token)

            # Skip if already posted
            if token in posted_sessions:
                continue

            embed = build_migration_embed(session)
            view = ApprovalView(self, token)

            try:
                msg = await channel.send(embed=embed, view=view)
                posted_sessions[token] = {
                    "message_id": msg.id,
                    "channel_id": channel.id,
                    "status": "pending",
                    "posted_at": time.time(),
                    "old_name": session.get("old_name"),
                    "new_name": session.get("new_name"),
                }
                dirty = True
                logger.info("Posted approval embed for session %s (msg %s)", token, msg.id)
            except discord.HTTPException as exc:
                logger.error("Failed to post approval embed for session %s: %s", token, exc)

        # 2. Check for sessions that disappeared from pending (resolved or expired)
        for token, meta in list(posted_sessions.items()):
            if not isinstance(meta, dict):
                continue

            if meta.get("status") == "pending" and token not in active_tokens:
                msg_id = meta.get("message_id")
                chan_id = meta.get("channel_id") or channel.id

                target_channel = self.bot.get_channel(chan_id)
                if target_channel and isinstance(target_channel, (discord.abc.Messageable, discord.TextChannel, discord.Thread)):
                    try:
                        msg = await target_channel.fetch_message(msg_id)
                        if msg and msg.embeds:
                            updated_embed = mark_embed_resolved(msg.embeds[0])
                            disabled_view = ApprovalView(self, token, disabled=True)
                            await msg.edit(embed=updated_embed, view=disabled_view)
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                        logger.debug("Could not update resolved message %s: %s", msg_id, exc)

                meta["status"] = "resolved"
                meta["resolved_at"] = time.time()
                dirty = True
                logger.info("Session %s resolved/expired elsewhere. Embed updated.", token)

        if dirty:
            await self.config.posted_sessions.set(posted_sessions)

    # -------------------------------------------------------------------------
    # Admin Commands ([p]hclinkset)
    # -------------------------------------------------------------------------

    @commands.group(name="hclinkset")
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def hclinkset(self, ctx: commands.Context) -> None:
        """Configuration commands for HC Account Migration Staff Approvals."""

    @hclinkset.command(name="backend", aliases=["url"])
    async def set_backend(self, ctx: commands.Context, url: str) -> None:
        """Set the base URL for the HC Account Migration backend."""
        url_clean = url.strip().rstrip("/")
        if not (url_clean.startswith("http://") or url_clean.startswith("https://")):
            await ctx.send("❌ URL must begin with `http://` or `https://`.")
            return

        await self.config.backend_url.set(url_clean)
        self.client.update_config(base_url=url_clean)
        await ctx.send(f"✅ Backend URL set to: `{url_clean}`")

    @hclinkset.command(name="secret")
    async def set_secret(self, ctx: commands.Context, *, secret: Optional[str] = None) -> None:
        """
        Set the HMAC shared secret.
        If called without arguments, prompts via DM so the secret is never echoed in chat.
        """
        # If secret passed directly in channel, try to delete message immediately for safety
        if secret is not None:
            if ctx.guild and ctx.channel.permissions_for(ctx.guild.me).manage_messages:
                try:
                    await ctx.message.delete()
                except discord.HTTPException:
                    pass

            secret_clean = secret.strip()
            await self.config.shared_secret.set(secret_clean)
            self.client.update_config(shared_secret=secret_clean)
            await ctx.send("✅ HMAC shared secret updated. (Command message was purged for security).")
            return

        # Prompt securely via DM
        try:
            dm_channel = await ctx.author.create_dm()
            await dm_channel.send(
                "🔒 **HCLink Shared Secret Setup**\n"
                "Please reply with the HMAC shared secret for the migration backend.\n"
                "*(You have 60 seconds to reply)*"
            )
            await ctx.send("📩 I've sent you a direct message to securely set the shared secret.")
        except discord.Forbidden:
            await ctx.send("❌ Could not send you a direct message. Please enable DMs from server members.")
            return

        def check(m: discord.Message) -> bool:
            return m.author == ctx.author and m.channel == dm_channel

        try:
            msg = await self.bot.wait_for("message", check=check, timeout=60.0)
            secret_clean = msg.content.strip()
            if not secret_clean:
                await dm_channel.send("❌ Secret cannot be empty.")
                return

            await self.config.shared_secret.set(secret_clean)
            self.client.update_config(shared_secret=secret_clean)
            await dm_channel.send("✅ HMAC shared secret successfully configured!")
        except asyncio.TimeoutError:
            await dm_channel.send("⌛ Timed out waiting for secret. Please run `[p]hclinkset secret` again.")

    @hclinkset.command(name="channel")
    async def set_channel(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        """Set the Discord text channel where migration approval embeds will be posted."""
        perms = channel.permissions_for(ctx.guild.me)
        if not perms.send_messages or not perms.embed_links:
            await ctx.send(
                f"⚠️ Warning: I lack permission to send messages or embed links in {channel.mention}. "
                "Please adjust permissions before using."
            )

        await self.config.channel_id.set(channel.id)
        await ctx.send(f"✅ Staff approval channel set to: {channel.mention}")

    @hclinkset.command(name="role")
    async def set_role(self, ctx: commands.Context, role: discord.Role) -> None:
        """Set the staff role allowed to use Approve/Reject/View buttons."""
        await self.config.staff_role_id.set(role.id)
        await ctx.send(f"✅ Staff reviewer role set to: `{role.name}` (ID: {role.id})")

    @hclinkset.command(name="interval")
    async def set_interval(self, ctx: commands.Context, seconds: int) -> None:
        """Set polling interval for checking pending migration requests (seconds)."""
        if seconds < 5:
            await ctx.send("❌ Interval must be at least 5 seconds.")
            return
        if seconds > 3600:
            await ctx.send("❌ Interval cannot exceed 3600 seconds (1 hour).")
            return

        await self.config.poll_interval.set(seconds)
        if self.poller_task.is_running():
            self.poller_task.change_interval(seconds=seconds)
        await ctx.send(f"✅ Polling interval set to `{seconds}` seconds.")

    @hclinkset.command(name="settings", aliases=["show"])
    async def show_settings(self, ctx: commands.Context) -> None:
        """Display current HCLinkApprovals configuration (secrets masked)."""
        backend_url = await self.config.backend_url()
        secret = await self.config.shared_secret()
        channel_id = await self.config.channel_id()
        role_id = await self.config.staff_role_id()
        poll_interval = await self.config.poll_interval()
        posted = await self.config.posted_sessions()

        channel_str = f"<#{channel_id}> ({channel_id})" if channel_id else "*Not configured*"
        role_str = f"<@&{role_id}> ({role_id})" if role_id else "*Not configured*"
        secret_status = "✅ Configured" if secret else "❌ Missing (Set with `[p]hclinkset secret`)"
        poller_status = "🟢 Running" if self.poller_task.is_running() else "🔴 Stopped"

        embed = discord.Embed(
            title="HCLink Approvals — Configuration",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Backend URL", value=f"`{backend_url}`", inline=False)
        embed.add_field(name="HMAC Shared Secret", value=secret_status, inline=True)
        embed.add_field(name="Poll Interval", value=f"`{poll_interval}s`", inline=True)
        embed.add_field(name="Poller State", value=poller_status, inline=True)
        embed.add_field(name="Staff Channel", value=channel_str, inline=False)
        embed.add_field(name="Staff Role", value=role_str, inline=False)
        embed.add_field(
            name="Session Tracking",
            value=f"`{len(posted)}` recorded sessions in store",
            inline=False,
        )

        await ctx.send(embed=embed)

    @hclinkset.command(name="test")
    async def test_connection(self, ctx: commands.Context) -> None:
        """Test backend connectivity and HMAC authentication."""
        backend_url = await self.config.backend_url()
        secret = await self.config.shared_secret()
        channel_id = await self.config.channel_id()

        if not secret:
            await ctx.send("❌ Shared secret is not set. Run `[p]hclinkset secret` first.")
            return

        status_msg = await ctx.send(f"Testing connectivity to `{backend_url}`...")

        start_time = time.monotonic()
        ok, detail, pending_count = await self.client.health_check()
        elapsed_ms = (time.monotonic() - start_time) * 1000

        embed = discord.Embed(
            title="HCLink Backend Diagnostic",
            color=discord.Color.green() if ok else discord.Color.red(),
        )
        embed.add_field(name="Target URL", value=f"`{backend_url}`", inline=False)
        embed.add_field(name="HMAC Auth & API", value="✅ Passed" if ok else "❌ Failed", inline=True)
        embed.add_field(name="Latency", value=f"{elapsed_ms:.1f}ms", inline=True)
        embed.add_field(name="Status Message", value=detail, inline=False)

        if channel_id:
            chan = self.bot.get_channel(channel_id)
            if chan:
                perms = chan.permissions_for(ctx.guild.me)
                can_post = perms.send_messages and perms.embed_links
                chan_status = "✅ Read/Write/Embed OK" if can_post else "❌ Missing Send/Embed Permissions"
            else:
                chan_status = "❌ Channel ID not found in guild cache"
            embed.add_field(name="Channel Check", value=f"<#{channel_id}>: {chan_status}", inline=False)

        await status_msg.edit(content=None, embed=embed)
