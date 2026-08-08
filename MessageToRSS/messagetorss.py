import re
import ssl
import asyncio
import aiohttp
from aiohttp import web
import discord
import logging
from datetime import datetime, timezone

from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.data_manager import cog_data_path

try:
    from feedgen.feed import FeedGenerator
    FEEDGEN_INSTALLED = True
except ImportError:
    FEEDGEN_INSTALLED = False

log = logging.getLogger("red.messagetorss")


class MessageToRSS(commands.Cog):
    """Convert Discord messages to RSS feeds."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=20260806, force_registration=True)

        self.config.register_guild(
            channels=[],
            feeds={},
            global_filters=[],
            global_removals=[],
            enabled=True,
            include_bot=False,
            include_embeds=True,
            feed_storage="local",
            remote_urls={},
            max_items=50,
            emoji_mode="html",
        )
        self.config.register_global(
            http_port=8823,
            http_host="0.0.0.0",
            actual_port=8823,
            https_enabled=True,
            https_port=443,
            ssl_cert="/etc/letsencrypt/live/feeds.cfourinternational.org/fullchain.pem",
            ssl_key="/etc/letsencrypt/live/feeds.cfourinternational.org/privkey.pem",
        )

        self._runner = None
        if not FEEDGEN_INSTALLED:
            log.warning("MessageToRSS: feedgen is not installed. Feed generation will not work. Install with `pip install feedgen==1.0.0`.")

        self.bot.loop.create_task(self._start_http_server())

    async def cog_unload(self):
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            # Give the OS a moment to release the sockets
            await asyncio.sleep(1)

    # Data Privacy
    async def red_delete_data_for_user(self, *, requester: str, user_id: int):
        all_guilds = await self.config.all_guilds()
        for guild_id, guild_data in all_guilds.items():
            modified = False
            async with self.config.guild(discord.Object(id=guild_id)).feeds() as feeds:
                for feed_name, feed_data in feeds.items():
                    if "items" in feed_data:
                        original_len = len(feed_data["items"])
                        feed_data["items"] = [item for item in feed_data["items"] if item.get("user_id") != user_id]
                        if len(feed_data["items"]) < original_len:
                            modified = True

            if modified:
                guild_obj = self.bot.get_guild(guild_id)
                if guild_obj:
                    updated_guild_data = await self.config.guild(guild_obj).all()
                    for feed_name, feed_data in updated_guild_data["feeds"].items():
                        await self._render_and_store_feed(guild_obj, feed_name, feed_data, feed_data.get("items", []), updated_guild_data)

    async def red_get_data_for_user(self, *, user_id: int):
        """Return a dict of all data stored for a user."""
        data = {}
        all_guilds = await self.config.all_guilds()
        for guild_id, guild_data in all_guilds.items():
            for feed_name, feed_data in guild_data.get("feeds", {}).items():
                items = feed_data.get("items", [])
                user_items = [item for item in items if item.get("user_id") == user_id]
                if user_items:
                    data[f"{guild_id}:{feed_name}"] = user_items
        return data

    # HTTP Server
    # Rate limiting is not required for v1 but could be added here in the future
    async def _handle_feed(self, request):
        feed_name = request.match_info.get("feed_name", "")
        if not feed_name:
            return web.Response(status=404, text="Not Found")

        feed_file = cog_data_path(self) / "feeds" / f"{feed_name}.xml"
        if not feed_file.exists():
            return web.Response(status=404, text="Feed not found")

        return web.FileResponse(feed_file, headers={"Content-Type": "application/rss+xml; charset=utf-8"})

    async def _handle_index(self, request):
        feed_dir = cog_data_path(self) / "feeds"
        feeds = []
        if feed_dir.exists():
            for f in feed_dir.iterdir():
                if f.name.endswith(".xml"):
                    feeds.append(f.name[:-4])

        html = "<html><body><h1>Available Feeds</h1><ul>"
        for f in feeds:
            html += f'<li><a href="/feeds/{f}">{f}</a></li>'
        html += "</ul></body></html>"
        return web.Response(text=html, content_type="text/html")

    async def _handle_health(self, request):
        return web.json_response({"status": "ok"})

    async def _handle_root(self, request):
        """Redirect / to /feeds (the feed index page)."""
        raise web.HTTPFound("/feeds")

    async def _start_http_server(self):
        app = web.Application()
        app.router.add_get('/', self._handle_root)
        app.router.add_get('/feeds', self._handle_index)
        app.router.add_get('/feeds/', self._handle_index)
        app.router.add_get('/feeds/{feed_name}', self._handle_feed)
        app.router.add_get('/health', self._handle_health)

        self._runner = web.AppRunner(app)
        await self._runner.setup()

        host = await self.config.http_host()
        base_port = await self.config.http_port()
        https_enabled = await self.config.https_enabled()
        https_port = await self.config.https_port()

        # Start HTTPS server on port 443 (or configured https_port)
        if https_enabled:
            cert_path = await self.config.ssl_cert()
            key_path = await self.config.ssl_key()

            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            try:
                ssl_context.load_cert_chain(cert_path, key_path)
            except Exception as e:
                log.error(f"MessageToRSS: Failed to load SSL cert: {e}. Falling back to HTTP only.")
                https_enabled = False

            if https_enabled:
                for p in range(https_port, https_port + 3):
                    try:
                        site = web.TCPSite(self._runner, host, p, ssl_context=ssl_context)
                        await site.start()
                        await self.config.actual_port.set(p)
                        log.info(f"MessageToRSS HTTPS server started on {host}:{p}")
                        return
                    except OSError:
                        log.warning(f"Port {p} in use, trying next...")

                log.error("Failed to start MessageToRSS HTTPS server, ports in use. Falling back to HTTP.")

        # Fallback: Start HTTP server on configured port
        for p in range(base_port, base_port + 3):
            try:
                site = web.TCPSite(self._runner, host, p)
                await site.start()
                await self.config.actual_port.set(p)
                log.info(f"MessageToRSS HTTP server started on {host}:{p}")
                return
            except OSError:
                log.warning(f"Port {p} in use, trying next...")

        log.error("Failed to start MessageToRSS HTTP server, ports in use.")

    # Event Listener
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.is_system():
            return

        log.info(f"MessageToRSS: on_message fired for {message.id} in #{message.channel.name} (guild {message.guild.id})")

        guild_data = await self.config.guild(message.guild).all()
        if not guild_data["enabled"]:
            return

        if message.channel.id not in guild_data["channels"]:
            return

        if message.author.bot and not guild_data["include_bot"]:
            return

        combined_text = message.content
        if guild_data["include_embeds"]:
            for embed in message.embeds:
                if embed.description:
                    combined_text += f"\n{embed.description}"
                if embed.title:
                    combined_text += f"\n{embed.title}"

        for feed_name, feed_config in guild_data["feeds"].items():
            # If feed has specific channels bound, only route messages from those channels
            feed_channels = feed_config.get("channels", [])
            if feed_channels and message.channel.id not in feed_channels:
                continue

            feed_filters = feed_config.get("filters", [])
            global_filters = guild_data.get("global_filters", [])

            if self._passes_filters(message, combined_text, feed_filters, global_filters):
                await self.push_to_feed(message.guild, feed_name, message, combined_text, guild_data)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        """Update feed items when a message is edited.

        Uses on_raw_message_edit to catch edits even for messages not in the
        bot's internal cache (e.g., messages sent before the bot started).
        If the message predates the feed (no existing item found), it gets
        backfilled as a new item on edit.
        """
        if not payload.guild_id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        guild_data = await self.config.guild(guild).all()
        if not guild_data["enabled"]:
            return

        channel_id = payload.channel_id
        if channel_id not in guild_data["channels"]:
            return

        # Fetch the full message from Discord
        channel = guild.get_channel(channel_id)
        if channel is None:
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

        if message.author.bot and not guild_data["include_bot"]:
            return

        # Rebuild combined text from the edited message
        combined_text = message.content
        if guild_data["include_embeds"]:
            for embed in message.embeds:
                if embed.description:
                    combined_text += f"\n{embed.description}"
                if embed.title:
                    combined_text += f"\n{embed.title}"

        item_id = f"discord://{guild.id}/{channel_id}/{message.id}"
        modified = False
        feeds_with_item = set()

        log.info(f"MessageToRSS: on_raw_message_edit fired for {item_id} in #{channel.name}")

        async with self.config.guild(guild).feeds() as feeds:
            for feed_name, feed_config in feeds.items():
                # Check channel binding
                feed_channels = feed_config.get("channels", [])
                if feed_channels and channel_id not in feed_channels:
                    continue

                items = feed_config.get("items", [])
                for item in items:
                    if item["id"] == item_id:
                        emoji_mode = guild_data.get("emoji_mode", "html")
                        edited_text = self._process_emojis(combined_text or "", emoji_mode)
                        global_removals = guild_data.get("global_removals", [])
                        feed_removals = feed_config.get("removals", [])
                        edited_text = self._apply_removals(edited_text, global_removals + feed_removals)
                        item["content"] = edited_text or "(No Text Content)"
                        item["title"] = f"Message from {message.author} in #{channel.name}"
                        modified = True
                        feeds_with_item.add(feed_name)
                        break

        # For feeds that didn't have the item (message predates feed), backfill it
        global_filters = guild_data.get("global_filters", [])
        backfilled = False
        for feed_name, feed_config in guild_data["feeds"].items():
            if feed_name in feeds_with_item:
                continue
            feed_channels = feed_config.get("channels", [])
            if feed_channels and channel_id not in feed_channels:
                continue
            feed_filters = feed_config.get("filters", [])
            if self._passes_filters(message, combined_text, feed_filters, global_filters):
                log.info(f"MessageToRSS: Backfilling message {item_id} into feed '{feed_name}'")
                await self.push_to_feed(guild, feed_name, message, combined_text, guild_data)
                backfilled = True

        if modified or backfilled:
            # Re-render all affected feeds
            updated_guild_data = await self.config.guild(guild).all()
            for feed_name, feed_data in updated_guild_data["feeds"].items():
                feed_channels = feed_data.get("channels", [])
                if feed_channels and channel_id not in feed_channels:
                    continue
                await self._render_and_store_feed(guild, feed_name, feed_data, feed_data.get("items", []), updated_guild_data)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        """Remove feed items when a message is deleted."""
        if not message.guild:
            return

        guild_data = await self.config.guild(message.guild).all()
        if not guild_data["enabled"]:
            return

        item_id = f"discord://{message.guild.id}/{message.channel.id}/{message.id}"
        modified_feeds = []

        async with self.config.guild(message.guild).feeds() as feeds:
            for feed_name, feed_config in feeds.items():
                items = feed_config.get("items", [])
                original_len = len(items)
                feed_config["items"] = [item for item in items if item["id"] != item_id]
                if len(feed_config["items"]) < original_len:
                    modified_feeds.append(feed_name)

        if modified_feeds:
            updated_guild_data = await self.config.guild(message.guild).all()
            for feed_name in modified_feeds:
                feed_data = updated_guild_data["feeds"][feed_name]
                await self._render_and_store_feed(message.guild, feed_name, feed_data, feed_data.get("items", []), updated_guild_data)

    def _passes_filters(self, message: discord.Message, combined_text: str, feed_filters: list, global_filters: list) -> bool:
        all_filters = global_filters + feed_filters
        whitelists = [f for f in all_filters if f["mode"] == "whitelist"]
        blacklists = [f for f in all_filters if f["mode"] == "blacklist"]

        for bf in blacklists:
            if self._matches_filter(message, combined_text, bf):
                return False

        if whitelists:
            all_match = True
            for wf in whitelists:
                if not self._matches_filter(message, combined_text, wf):
                    all_match = False
                    break
            if not all_match:
                return False

        return True

    def _matches_filter(self, message: discord.Message, combined_text: str, f: dict) -> bool:
        f_type = f.get("type")
        f_val = str(f.get("value"))
        case_sens = f.get("case_sensitive", False)

        if f_type == "keyword":
            text = combined_text if case_sens else combined_text.lower()
            val = f_val if case_sens else f_val.lower()
            return val in text
        elif f_type == "regex":
            flags = 0 if case_sens else re.IGNORECASE
            try:
                return bool(re.search(f_val, combined_text, flags))
            except re.error:
                return False
        elif f_type == "user":
            try:
                return message.author.id == int(f_val)
            except ValueError:
                return False
        elif f_type == "role":
            if isinstance(message.author, discord.Member):
                try:
                    role_id = int(f_val)
                    return any(r.id == role_id for r in message.author.roles)
                except ValueError:
                    return False
            return False
        return False

    def _process_emojis(self, text: str, emoji_mode: str) -> str:
        """Convert Discord custom emojis based on the configured mode.

        Modes:
        - html:  <:name:id> → <img> tag (renders in most RSS readers)
        - text:  <:name:id> → :name: (human-readable, no images)
        - raw:   <:name:id> → unchanged
        """
        if emoji_mode == "raw":
            return text

        # Match both animated <a:name:id> and static <:name:id>
        emoji_pattern = re.compile(r'<(a?):(\w+):(\d+)>')

        if emoji_mode == "html":
            def replace_emoji(m):
                animated = m.group(1) == 'a'
                name = m.group(2)
                emoji_id = m.group(3)
                ext = 'gif' if animated else 'png'
                return f'<img src="https://cdn.discordapp.com/emojis/{emoji_id}.{ext}" alt=":{name}:" style="height:1.5em;width:1.5em;vertical-align:middle">'
            return emoji_pattern.sub(replace_emoji, text)

        elif emoji_mode == "text":
            def replace_emoji(m):
                name = m.group(2)
                return f':{name}:'
            return emoji_pattern.sub(replace_emoji, text)

        return text

    def _apply_removals(self, text: str, removals: list) -> str:
        """Remove configured text patterns from message content.

        Each removal is a dict:
        - type: "text" | "regex"
        - value: the pattern to remove
        - case_sensitive: bool (default False, ignored for regex which uses re.IGNORECASE)
        - replacement: str (default "" — what to replace with)
        """
        for r in removals:
            r_type = r.get("type", "text")
            r_val = str(r.get("value", ""))
            case_sens = r.get("case_sensitive", False)
            replacement = r.get("replacement", "")

            if r_type == "text":
                if case_sens:
                    text = text.replace(r_val, replacement)
                else:
                    # Case-insensitive replace
                    pattern = re.compile(re.escape(r_val), re.IGNORECASE)
                    text = pattern.sub(replacement, text)
            elif r_type == "regex":
                flags = 0 if case_sens else re.IGNORECASE
                try:
                    text = re.sub(r_val, replacement, text, flags=flags)
                except re.error:
                    pass

        return text

    async def push_to_feed(self, guild, feed_name, message, combined_text, guild_data):
        emoji_mode = guild_data.get("emoji_mode", "html")
        processed_text = self._process_emojis(combined_text or "", emoji_mode)
        # Apply text removals (global + per-feed)
        global_removals = guild_data.get("global_removals", [])
        feed_removals = guild_data.get("feeds", {}).get(feed_name, {}).get("removals", [])
        processed_text = self._apply_removals(processed_text, global_removals + feed_removals)
        item_dict = {
            "id": f"discord://{guild.id}/{message.channel.id}/{message.id}",
            "title": f"Message from {message.author} in #{message.channel.name}",
            "content": processed_text or "(No Text Content)",
            "published": message.created_at.replace(tzinfo=timezone.utc).isoformat(),
            "author_name": str(message.author),
            "link": message.jump_url,
            "user_id": message.author.id
        }

        async with self.config.guild(guild).feeds() as feeds:
            if "items" not in feeds[feed_name]:
                feeds[feed_name]["items"] = []

            feeds[feed_name]["items"].append(item_dict)
            max_items = guild_data.get("max_items", 50)

            if len(feeds[feed_name]["items"]) > max_items:
                feeds[feed_name]["items"] = feeds[feed_name]["items"][-max_items:]

            items_to_render = feeds[feed_name]["items"]

        await self._render_and_store_feed(guild, feed_name, feeds[feed_name], items_to_render, guild_data)

    async def _render_and_store_feed(self, guild, feed_name, feed_config, items, guild_data):
        if not FEEDGEN_INSTALLED:
            return

        fg = FeedGenerator()
        fg.title(feed_config.get("title", feed_name))
        fg.description(feed_config.get("description", "Discord RSS Feed"))

        host = await self.config.http_host()
        port = await self.config.actual_port()
        feed_url = feed_config.get("url", f"http://{host}:{port}/feeds/{feed_name}")

        fg.link(href=feed_url, rel='self')
        fg.language('en')

        for item in items:
            fe = fg.add_entry()
            fe.id(item["id"])
            fe.title(item["title"])
            fe.content(item["content"])
            fe.published(item["published"])
            fe.author(name=item["author_name"])
            fe.link(href=item["link"], rel='alternate')

        try:
            xml_content = fg.rss_str(pretty=True)
        except Exception as e:
            log.error(f"Error generating feed {feed_name}: {e}")
            return

        storage_mode = guild_data.get("feed_storage", "local")

        if storage_mode == "local":
            feed_dir = cog_data_path(self) / "feeds"
            feed_dir.mkdir(parents=True, exist_ok=True)
            feed_file = feed_dir / f"{feed_name}.xml"
            feed_file.write_bytes(xml_content)
        elif storage_mode == "remote":
            remote_urls = guild_data.get("remote_urls", {})
            url = remote_urls.get(feed_name)
            if url:
                try:
                    async with aiohttp.ClientSession() as session:
                        await session.post(url, data=xml_content, headers={"Content-Type": "application/rss+xml"})
                except Exception as e:
                    log.warning(f"Failed to push feed {feed_name} to remote url {url}: {e}")

    # ===== Legacy Prefix Commands =====

    @commands.group(name="messagetorss")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def messagetorss(self, ctx: commands.Context):
        """MessageToRSS configuration commands."""
        if ctx.invoked_subcommand is None:
            await ctx.send("Use `[p]help messagetorss` to see available commands.")

    @messagetorss.command(name="addchannel")
    async def cmd_addchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Add a channel to the listen list."""
        async with self.config.guild(ctx.guild).channels() as channels:
            if channel.id not in channels:
                channels.append(channel.id)
                await ctx.send(f"Added {channel.mention} to listen list.")
            else:
                await ctx.send(f"{channel.mention} is already in the listen list.")

    @messagetorss.command(name="removechannel")
    async def cmd_removechannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Remove a channel from the listen list."""
        async with self.config.guild(ctx.guild).channels() as channels:
            if channel.id in channels:
                channels.remove(channel.id)
                await ctx.send(f"Removed {channel.mention} from listen list.")
            else:
                await ctx.send(f"{channel.mention} is not in the listen list.")

    @messagetorss.command(name="listchannels")
    async def cmd_listchannels(self, ctx: commands.Context):
        """List all listened channels."""
        channels = await self.config.guild(ctx.guild).channels()
        if not channels:
            await ctx.send("No channels are being listened to.")
            return
        mentions = [f"<#{c}>" for c in channels]
        await ctx.send(f"Listened channels: {', '.join(mentions)}")

    @messagetorss.command(name="addfeed")
    async def cmd_addfeed(self, ctx: commands.Context, name: str, title: str, *, description: str = ""):
        """Create a new RSS feed.

        Usage: `[p]messagetorss addfeed <name> <title> [description]`
        """
        async with self.config.guild(ctx.guild).feeds() as feeds:
            if name in feeds:
                await ctx.send(f"Feed '{name}' already exists.")
                return
            feeds[name] = {
                "title": title,
                "description": description,
                "filters": [],
                "items": [],
                "channels": [],
                "removals": []
            }
        await ctx.send(f"Created feed '{name}'.")

    @messagetorss.command(name="removefeed")
    async def cmd_removefeed(self, ctx: commands.Context, name: str):
        """Delete an RSS feed."""
        async with self.config.guild(ctx.guild).feeds() as feeds:
            if name in feeds:
                del feeds[name]
                await ctx.send(f"Removed feed '{name}'.")
            else:
                await ctx.send(f"Feed '{name}' not found.")

    @messagetorss.command(name="listfeeds")
    async def cmd_listfeeds(self, ctx: commands.Context):
        """List all configured feeds."""
        feeds = await self.config.guild(ctx.guild).feeds()
        if not feeds:
            await ctx.send("No feeds configured.")
            return
        msg = "Configured feeds:\n"
        for name, config in feeds.items():
            bound = config.get("channels", [])
            bound_str = f" [bound: {', '.join(f'<#{c}>' for c in bound)}]" if bound else " [all channels]"
            msg += f"- **{name}**: {config.get('title')} ({len(config.get('items', []))} items){bound_str}\n"
        await ctx.send(msg)

    @messagetorss.command(name="bindchannel")
    async def cmd_bindchannel(self, ctx: commands.Context, feed_name: str, channel: discord.TextChannel):
        """Bind a specific channel to a feed.

        Only messages from bound channels will be added to that feed.
        If no channels are bound, the feed receives from all guild-level listened channels.

        Usage: `[p]messagetorss bindchannel <feed_name> <channel>`
        """
        async with self.config.guild(ctx.guild).feeds() as feeds:
            if feed_name not in feeds:
                await ctx.send(f"Feed '{feed_name}' not found.")
                return
            feed_channels = feeds[feed_name].setdefault("channels", [])
            if channel.id in feed_channels:
                await ctx.send(f"{channel.mention} is already bound to feed '{feed_name}'.")
                return
            feed_channels.append(channel.id)
        await ctx.send(f"Bound {channel.mention} to feed '{feed_name}'.")

    @messagetorss.command(name="unbindchannel")
    async def cmd_unbindchannel(self, ctx: commands.Context, feed_name: str, channel: discord.TextChannel):
        """Remove a channel binding from a feed.

        Usage: `[p]messagetorss unbindchannel <feed_name> <channel>`
        """
        async with self.config.guild(ctx.guild).feeds() as feeds:
            if feed_name not in feeds:
                await ctx.send(f"Feed '{feed_name}' not found.")
                return
            feed_channels = feeds[feed_name].get("channels", [])
            if channel.id in feed_channels:
                feed_channels.remove(channel.id)
                await ctx.send(f"Unbound {channel.mention} from feed '{feed_name}'.")
            else:
                await ctx.send(f"{channel.mention} is not bound to feed '{feed_name}'.")

    @messagetorss.command(name="listbindings")
    async def cmd_listbindings(self, ctx: commands.Context, feed_name: str = None):
        """List channel bindings for a feed or all feeds.

        Usage: `[p]messagetorss listbindings [feed_name]`
        """
        feeds = await self.config.guild(ctx.guild).feeds()
        if not feeds:
            await ctx.send("No feeds configured.")
            return
        msg = ""
        for name, config in feeds.items():
            if feed_name and name != feed_name:
                continue
            bound = config.get("channels", [])
            if bound:
                mentions = ", ".join(f"<#{c}>" for c in bound)
                msg += f"**{name}**: {mentions}\n"
            else:
                msg += f"**{name}**: (all guild channels)\n"
        if not msg.strip():
            msg = f"Feed '{feed_name}' not found." if feed_name else "No feeds configured."
        await ctx.send(msg)

    @messagetorss.command(name="addfilter")
    async def cmd_addfilter(self, ctx: commands.Context, feed_name: str, filter_type: str, mode: str, value: str, case_sensitive: bool = False):
        """Add a content filter to a feed.

        Usage: `[p]messagetorss addfilter <feed_name> <keyword|regex|user|role> <whitelist|blacklist> <value> [case_sensitive]`

        Examples:
        - `[p]messagetorss addfilter myfeed keyword whitelist "breaking news" False`
        - `[p]messagetorss addfilter myfeed regex blacklist "spam|test" True`
        - `[p]messagetorss addfilter myfeed user whitelist 123456789`
        """
        valid_types = ["keyword", "regex", "user", "role"]
        valid_modes = ["whitelist", "blacklist"]

        if filter_type not in valid_types:
            await ctx.send(f"Invalid filter type. Must be one of: {', '.join(valid_types)}")
            return
        if mode not in valid_modes:
            await ctx.send(f"Invalid mode. Must be one of: {', '.join(valid_modes)}")
            return

        if filter_type == "regex":
            try:
                re.compile(value)
            except re.error:
                await ctx.send("Invalid regex pattern.")
                return

        async with self.config.guild(ctx.guild).feeds() as feeds:
            if feed_name not in feeds:
                await ctx.send(f"Feed '{feed_name}' not found.")
                return

            feeds[feed_name].setdefault("filters", []).append({
                "type": filter_type,
                "mode": mode,
                "value": value,
                "case_sensitive": case_sensitive
            })
        await ctx.send(f"Added {mode} {filter_type} filter to '{feed_name}'.")

    @messagetorss.command(name="removefilter")
    async def cmd_removefilter(self, ctx: commands.Context, feed_name: str, filter_index: int):
        """Remove a filter from a feed by index.

        Usage: `[p]messagetorss removefilter <feed_name> <index>`
        Use `[p]messagetorss listfilters <feed_name>` to see filter indices.
        """
        async with self.config.guild(ctx.guild).feeds() as feeds:
            if feed_name not in feeds:
                await ctx.send(f"Feed '{feed_name}' not found.")
                return

            filters = feeds[feed_name].get("filters", [])
            if 0 <= filter_index < len(filters):
                removed = filters.pop(filter_index)
                await ctx.send(f"Removed filter: {removed['type']} {removed['mode']} '{removed['value']}'")
            else:
                await ctx.send("Invalid filter index.")

    @messagetorss.command(name="listfilters")
    async def cmd_listfilters(self, ctx: commands.Context, feed_name: str = None):
        """List filters for a feed or all feeds.

        Usage: `[p]messagetorss listfilters [feed_name]`
        """
        guild_data = await self.config.guild(ctx.guild).all()
        msg = ""
        if guild_data["global_filters"]:
            msg += "**Global Filters:**\n"
            for i, f in enumerate(guild_data["global_filters"]):
                msg += f"{i}: {f['mode']} {f['type']} -> `{f['value']}` (Case sensitive: {f.get('case_sensitive', False)})\n"

        feeds = guild_data["feeds"]
        for name, config in feeds.items():
            if feed_name and name != feed_name:
                continue
            filters = config.get("filters", [])
            if filters:
                msg += f"\n**Feed '{name}' Filters:**\n"
                for i, f in enumerate(filters):
                    msg += f"{i}: {f['mode']} {f['type']} -> `{f['value']}` (Case sensitive: {f.get('case_sensitive', False)})\n"

        if not msg.strip():
            msg = "No filters found."
        await ctx.send(msg)

    @messagetorss.command(name="addglobalfilter")
    async def cmd_addglobalfilter(self, ctx: commands.Context, filter_type: str, mode: str, value: str, case_sensitive: bool = False):
        """Add a guild-wide filter.

        Usage: `[p]messagetorss addglobalfilter <keyword|regex|user|role> <whitelist|blacklist> <value> [case_sensitive]`
        """
        valid_types = ["keyword", "regex", "user", "role"]
        valid_modes = ["whitelist", "blacklist"]

        if filter_type not in valid_types:
            await ctx.send(f"Invalid filter type. Must be one of: {', '.join(valid_types)}")
            return
        if mode not in valid_modes:
            await ctx.send(f"Invalid mode. Must be one of: {', '.join(valid_modes)}")
            return

        if filter_type == "regex":
            try:
                re.compile(value)
            except re.error:
                await ctx.send("Invalid regex pattern.")
                return

        async with self.config.guild(ctx.guild).global_filters() as filters:
            filters.append({
                "type": filter_type,
                "mode": mode,
                "value": value,
                "case_sensitive": case_sensitive
            })
        await ctx.send(f"Added global {mode} {filter_type} filter.")

    @messagetorss.command(name="removeglobalfilter")
    async def cmd_removeglobalfilter(self, ctx: commands.Context, filter_index: int):
        """Remove a guild-wide filter by index.

        Usage: `[p]messagetorss removeglobalfilter <index>`
        Use `[p]messagetorss listfilters` to see global filter indices.
        """
        async with self.config.guild(ctx.guild).global_filters() as filters:
            if 0 <= filter_index < len(filters):
                removed = filters.pop(filter_index)
                await ctx.send(f"Removed global filter: {removed['type']} {removed['mode']} '{removed['value']}'")
            else:
                await ctx.send("Invalid filter index.")

    @messagetorss.command(name="addremoval")
    async def cmd_addremoval(self, ctx: commands.Context, feed_name: str, removal_type: str, value: str, case_sensitive: bool = False, *, replacement: str = ""):
        """Add a text removal rule to a feed.

        Removes or replaces matching text from messages before they go into the feed.

        Usage: `[p]messagetorss addremoval <feed_name> <text|regex> <value> [case_sensitive] [replacement]`

        Examples:
        - `[p]messagetorss addremoval myfeed text "SECRET:" True` — removes "SECRET:" (case-sensitive)
        - `[p]messagetorss addremoval myfeed text "spoiler"` — removes "spoiler" (case-insensitive)
        - `[p]messagetorss addremoval myfeed regex "\\[SPOILER\\].*?\\[/SPOILER\\]" False ""` — removes spoiler tags
        - `[p]messagetorss addremoval myfeed text "DRAFT" True "FINAL"` — replaces DRAFT with FINAL
        """
        valid_types = ["text", "regex"]

        if removal_type not in valid_types:
            await ctx.send(f"Invalid removal type. Must be one of: {', '.join(valid_types)}")
            return

        if removal_type == "regex":
            try:
                re.compile(value)
            except re.error:
                await ctx.send("Invalid regex pattern.")
                return

        async with self.config.guild(ctx.guild).feeds() as feeds:
            if feed_name not in feeds:
                await ctx.send(f"Feed '{feed_name}' not found.")
                return

            feeds[feed_name].setdefault("removals", []).append({
                "type": removal_type,
                "value": value,
                "case_sensitive": case_sensitive,
                "replacement": replacement
            })
        rep_msg = f" replacing with '{replacement}'" if replacement else ""
        await ctx.send(f"Added {removal_type} removal to '{feed_name}': `{value}`{rep_msg}.")

    @messagetorss.command(name="removeremoval")
    async def cmd_removeremoval(self, ctx: commands.Context, feed_name: str, removal_index: int):
        """Remove a text removal rule from a feed by index.

        Usage: `[p]messagetorss removeremoval <feed_name> <index>`
        Use `[p]messagetorss listremovals <feed_name>` to see removal indices.
        """
        async with self.config.guild(ctx.guild).feeds() as feeds:
            if feed_name not in feeds:
                await ctx.send(f"Feed '{feed_name}' not found.")
                return

            removals = feeds[feed_name].get("removals", [])
            if 0 <= removal_index < len(removals):
                removed = removals.pop(removal_index)
                rep = f" -> '{removed['replacement']}'" if removed.get("replacement") else ""
                await ctx.send(f"Removed rule: {removed['type']} '{removed['value']}'{rep}")
            else:
                await ctx.send("Invalid removal index.")

    @messagetorss.command(name="listremovals")
    async def cmd_listremovals(self, ctx: commands.Context, feed_name: str = None):
        """List text removal rules for a feed or all feeds.

        Usage: `[p]messagetorss listremovals [feed_name]`
        """
        guild_data = await self.config.guild(ctx.guild).all()
        msg = ""

        if guild_data.get("global_removals"):
            msg += "**Global Removals:**\n"
            for i, r in enumerate(guild_data["global_removals"]):
                rep = f" -> '{r.get('replacement', '')}'" if r.get("replacement") else ""
                msg += f"{i}: {r['type']} `{r['value']}` (case sensitive: {r.get('case_sensitive', False)}){rep}\n"

        for name, config in guild_data["feeds"].items():
            if feed_name and name != feed_name:
                continue
            removals = config.get("removals", [])
            if removals:
                msg += f"\n**Feed '{name}' Removals:**\n"
                for i, r in enumerate(removals):
                    rep = f" -> '{r.get('replacement', '')}'" if r.get("replacement") else ""
                    msg += f"{i}: {r['type']} `{r['value']}` (case sensitive: {r.get('case_sensitive', False)}){rep}\n"

        if not msg.strip():
            msg = f"Feed '{feed_name}' not found." if feed_name else "No removal rules configured."
        await ctx.send(msg)

    @messagetorss.command(name="addglobalremoval")
    async def cmd_addglobalremoval(self, ctx: commands.Context, removal_type: str, value: str, case_sensitive: bool = False, *, replacement: str = ""):
        """Add a guild-wide text removal rule.

        Applies to all feeds in this guild.

        Usage: `[p]messagetorss addglobalremoval <text|regex> <value> [case_sensitive] [replacement]`

        Examples:
        - `[p]messagetorss addglobalremoval text "## " True` — removes markdown headers
        - `[p]messagetorss addglobalremoval regex "@\\w+#\\d+" False "[mention]"` — replaces user mentions
        """
        valid_types = ["text", "regex"]

        if removal_type not in valid_types:
            await ctx.send(f"Invalid removal type. Must be one of: {', '.join(valid_types)}")
            return

        if removal_type == "regex":
            try:
                re.compile(value)
            except re.error:
                await ctx.send("Invalid regex pattern.")
                return

        async with self.config.guild(ctx.guild).global_removals() as removals:
            removals.append({
                "type": removal_type,
                "value": value,
                "case_sensitive": case_sensitive,
                "replacement": replacement
            })
        rep_msg = f" replacing with '{replacement}'" if replacement else ""
        await ctx.send(f"Added global {removal_type} removal: `{value}`{rep_msg}.")

    @messagetorss.command(name="removeglobalremoval")
    async def cmd_removeglobalremoval(self, ctx: commands.Context, removal_index: int):
        """Remove a guild-wide text removal rule by index.

        Usage: `[p]messagetorss removeglobalremoval <index>`
        Use `[p]messagetorss listremovals` to see global removal indices.
        """
        async with self.config.guild(ctx.guild).global_removals() as removals:
            if 0 <= removal_index < len(removals):
                removed = removals.pop(removal_index)
                rep = f" -> '{removed.get('replacement', '')}'" if removed.get("replacement") else ""
                await ctx.send(f"Removed global rule: {removed['type']} '{removed['value']}'{rep}")
            else:
                await ctx.send("Invalid removal index.")

    @messagetorss.command(name="toggle")
    async def cmd_toggle(self, ctx: commands.Context):
        """Toggle the cog on/off for this guild."""
        current = await self.config.guild(ctx.guild).enabled()
        await self.config.guild(ctx.guild).enabled.set(not current)
        await ctx.send(f"MessageToRSS is now {'enabled' if not current else 'disabled'}.")

    @messagetorss.command(name="setincludebot")
    async def cmd_setincludebot(self, ctx: commands.Context, enabled: bool):
        """Set whether to include bot messages.

        Usage: `[p]messagetorss setincludebot <True|False>`
        """
        await self.config.guild(ctx.guild).include_bot.set(enabled)
        await ctx.send(f"Include bot messages set to {enabled}.")

    @messagetorss.command(name="setincludeembeds")
    async def cmd_setincludeembeds(self, ctx: commands.Context, enabled: bool):
        """Set whether to include embed content from messages.

        Usage: `[p]messagetorss setincludeembeds <True|False>`
        """
        await self.config.guild(ctx.guild).include_embeds.set(enabled)
        await ctx.send(f"Include embeds set to {enabled}.")

    @messagetorss.command(name="setemoji")
    async def cmd_setemoji(self, ctx: commands.Context, mode: str):
        """Set how custom emojis are rendered in RSS feeds.

        Modes:
        - `html`: Emojis as <img> tags (renders inline in most RSS readers)
        - `text`: Emojis as :name: (human-readable, no images)
        - `raw`:  Emojis as <:name:id> (Discord raw format, unchanged)

        Usage: `[p]messagetorss setemoji <html|text|raw>`
        """
        valid_modes = ["html", "text", "raw"]
        if mode not in valid_modes:
            await ctx.send(f"Invalid emoji mode. Must be one of: {', '.join(valid_modes)}")
            return
        await self.config.guild(ctx.guild).emoji_mode.set(mode)
        await ctx.send(f"Emoji mode set to `{mode}`.")

    @messagetorss.command(name="setmaxitems")
    async def cmd_setmaxitems(self, ctx: commands.Context, max_items: int):
        """Set max items per feed.

        Usage: `[p]messagetorss setmaxitems <number>`
        """
        if max_items <= 0:
            await ctx.send("Max items must be greater than 0.")
            return
        await self.config.guild(ctx.guild).max_items.set(max_items)
        await ctx.send(f"Max items per feed set to {max_items}.")

    @messagetorss.command(name="setstorage")
    async def cmd_setstorage(self, ctx: commands.Context, mode: str):
        """Set feed storage mode.

        Usage: `[p]messagetorss setstorage <local|remote>`
        """
        if mode not in ("local", "remote"):
            await ctx.send("Invalid storage mode. Must be 'local' or 'remote'.")
            return
        await self.config.guild(ctx.guild).feed_storage.set(mode)
        await ctx.send(f"Feed storage mode set to {mode}.")

    @messagetorss.command(name="setremoteurl")
    async def cmd_setremoteurl(self, ctx: commands.Context, feed_name: str, url: str):
        """Set remote URL for a feed (for remote storage mode).

        Usage: `[p]messagetorss setremoteurl <feed_name> <url>`
        """
        async with self.config.guild(ctx.guild).remote_urls() as urls:
            urls[feed_name] = url
        await ctx.send(f"Remote URL for feed '{feed_name}' set.")

    @messagetorss.command(name="showconfig")
    async def cmd_showconfig(self, ctx: commands.Context):
        """Show current configuration."""
        data = await self.config.guild(ctx.guild).all()
        msg = f"**Enabled:** {data['enabled']}\n"
        msg += f"**Storage:** {data['feed_storage']}\n"
        msg += f"**Max Items:** {data['max_items']}\n"
        msg += f"**Include Bot:** {data['include_bot']}\n"
        msg += f"**Include Embeds:** {data['include_embeds']}\n"
        msg += f"**Emoji Mode:** {data.get('emoji_mode', 'html')}\n"
        await ctx.send(msg)

    @messagetorss.command(name="feedpath")
    async def cmd_feedpath(self, ctx: commands.Context, feed_name: str):
        """Show the local file path for a feed.

        Usage: `[p]messagetorss feedpath <feed_name>`
        """
        feed_dir = cog_data_path(self) / "feeds"
        feed_file = feed_dir / f"{feed_name}.xml"
        await ctx.send(f"Local path: `{feed_file}`")

    @messagetorss.command(name="feedurl")
    async def cmd_feedurl(self, ctx: commands.Context, feed_name: str):
        """Show the full HTTP URL for accessing a feed.

        Usage: `[p]messagetorss feedurl <feed_name>`
        """
        https_enabled = await self.config.https_enabled()
        port = await self.config.actual_port()
        if https_enabled and port == 443:
            url = f"https://feeds.cfourinternational.org/feeds/{feed_name}"
        elif https_enabled:
            url = f"https://feeds.cfourinternational.org:{port}/feeds/{feed_name}"
        else:
            host = await self.config.http_host()
            url = f"http://{host}:{port}/feeds/{feed_name}"
        await ctx.send(f"Feed URL: {url}")

    @messagetorss.command(name="setport")
    @commands.is_owner()
    async def cmd_setport(self, ctx: commands.Context, port: int):
        """Change the HTTP server port (restarts server).

        Usage: `[p]messagetorss setport <port>`
        Bot owner only — this is a global setting.
        """
        await self.config.http_port.set(port)
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        self.bot.loop.create_task(self._start_http_server())
        await ctx.send(f"HTTP server restarting on base port {port}...")

    @messagetorss.command(name="serverstatus")
    async def cmd_serverstatus(self, ctx: commands.Context):
        """Show HTTP server status."""
        host = await self.config.http_host()
        port = await self.config.actual_port()
        https_enabled = await self.config.https_enabled()
        running = self._runner is not None

        feed_dir = cog_data_path(self) / "feeds"
        feed_count = 0
        if feed_dir.exists():
            feed_count = len([f for f in feed_dir.iterdir() if f.name.endswith(".xml")])

        scheme = "HTTPS" if https_enabled else "HTTP"
        msg = f"**Status:** {'Running' if running else 'Stopped'}\n"
        msg += f"**Protocol:** {scheme}\n"
        msg += f"**Host:** {host}\n"
        msg += f"**Port:** {port}\n"
        msg += f"**Feeds Served:** {feed_count}"
        await ctx.send(msg)