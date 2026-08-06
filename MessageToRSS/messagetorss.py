import re
import os
import aiohttp
from aiohttp import web
import discord
import logging
from datetime import datetime, timezone

from redbot.core import Config, commands, app_commands
from redbot.core.bot import Red
from redbot.core.data_manager import cog_data_path
from discord import app_commands as discord_app_commands

try:
    from feedgen.feed import FeedGenerator
    FEEDGEN_INSTALLED = True
except ImportError:
    FEEDGEN_INSTALLED = False

log = logging.getLogger("red.messagetorss")

class MessageToRSS(commands.Cog):
    """Convert Discord messages to RSS feeds."""
    
    messagetorss = discord_app_commands.Group(name="messagetorss", description="MessageToRSS configuration", default_permissions=discord.Permissions(manage_guild=True))
    
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=20260806, force_registration=True)
        
        self.config.register_guild(
            channels=[],
            feeds={},
            global_filters=[],
            enabled=True,
            include_bot=False,
            include_embeds=True,
            feed_storage="local",
            remote_urls={},
            max_items=50,
        )
        self.config.register_global(
            http_port=8823,
            http_host="0.0.0.0",
            actual_port=8823
        )
        
        self._runner = None
        if not FEEDGEN_INSTALLED:
            log.warning("MessageToRSS: feedgen is not installed. Feed generation will not work. Install with `pip install feedgen==1.0.0`.")
            
        self.bot.loop.create_task(self._start_http_server())
        
    async def cog_unload(self):
        if self._runner is not None:
            await self._runner.cleanup()

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
        
    async def _start_http_server(self):
        app = web.Application()
        app.router.add_get('/feeds', self._handle_index)
        app.router.add_get('/feeds/', self._handle_index)
        app.router.add_get('/feeds/{feed_name}', self._handle_feed)
        app.router.add_get('/health', self._handle_health)
        
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        
        host = await self.config.http_host()
        base_port = await self.config.http_port()
        
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
            feed_filters = feed_config.get("filters", [])
            global_filters = guild_data.get("global_filters", [])
            
            if self._passes_filters(message, combined_text, feed_filters, global_filters):
                await self.push_to_feed(message.guild, feed_name, message, combined_text, guild_data)
                
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

    async def push_to_feed(self, guild, feed_name, message, combined_text, guild_data):
        item_dict = {
            "id": f"discord://{guild.id}/{message.channel.id}/{message.id}",
            "title": f"Message from {message.author} in #{message.channel.name}",
            "content": combined_text or "(No Text Content)",
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

    @messagetorss.command(name="addchannel", description="Add a channel to the listen list")
    @discord_app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_addchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        async with self.config.guild(interaction.guild).channels() as channels:
            if channel.id not in channels:
                channels.append(channel.id)
                await interaction.response.send_message(f"Added {channel.mention} to listen list.")
            else:
                await interaction.response.send_message(f"{channel.mention} is already in the listen list.")

    @messagetorss.command(name="removechannel", description="Remove a channel from the listen list")
    @discord_app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_removechannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        async with self.config.guild(interaction.guild).channels() as channels:
            if channel.id in channels:
                channels.remove(channel.id)
                await interaction.response.send_message(f"Removed {channel.mention} from listen list.")
            else:
                await interaction.response.send_message(f"{channel.mention} is not in the listen list.")

    @messagetorss.command(name="listchannels", description="List all listened channels")
    @discord_app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_listchannels(self, interaction: discord.Interaction):
        channels = await self.config.guild(interaction.guild).channels()
        if not channels:
            await interaction.response.send_message("No channels are being listened to.")
            return
        mentions = [f"<#{c}>" for c in channels]
        await interaction.response.send_message(f"Listened channels: {', '.join(mentions)}")

    @messagetorss.command(name="addfeed", description="Create a new RSS feed")
    @discord_app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_addfeed(self, interaction: discord.Interaction, name: str, title: str, description: str = ""):
        async with self.config.guild(interaction.guild).feeds() as feeds:
            if name in feeds:
                await interaction.response.send_message(f"Feed '{name}' already exists.")
                return
            feeds[name] = {
                "title": title,
                "description": description,
                "filters": [],
                "items": []
            }
        await interaction.response.send_message(f"Created feed '{name}'.")

    @messagetorss.command(name="removefeed", description="Delete an RSS feed")
    @discord_app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_removefeed(self, interaction: discord.Interaction, name: str):
        async with self.config.guild(interaction.guild).feeds() as feeds:
            if name in feeds:
                del feeds[name]
                await interaction.response.send_message(f"Removed feed '{name}'.")
            else:
                await interaction.response.send_message(f"Feed '{name}' not found.")

    @messagetorss.command(name="listfeeds", description="List all configured feeds")
    @discord_app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_listfeeds(self, interaction: discord.Interaction):
        feeds = await self.config.guild(interaction.guild).feeds()
        if not feeds:
            await interaction.response.send_message("No feeds configured.")
            return
        msg = "Configured feeds:\n"
        for name, config in feeds.items():
            msg += f"- **{name}**: {config.get('title')} ({len(config.get('items', []))} items)\n"
        await interaction.response.send_message(msg)

    @messagetorss.command(name="addfilter", description="Add a content filter to a feed")
    @discord_app_commands.choices(
        type=[
            discord_app_commands.Choice(name="keyword", value="keyword"),
            discord_app_commands.Choice(name="regex", value="regex"),
            discord_app_commands.Choice(name="user", value="user"),
            discord_app_commands.Choice(name="role", value="role"),
        ],
        mode=[
            discord_app_commands.Choice(name="whitelist", value="whitelist"),
            discord_app_commands.Choice(name="blacklist", value="blacklist"),
        ]
    )
    @discord_app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_addfilter(self, interaction: discord.Interaction, feed_name: str, type: discord_app_commands.Choice[str], mode: discord_app_commands.Choice[str], value: str, case_sensitive: bool = False):
        if type.value == "regex":
            try:
                re.compile(value)
            except re.error:
                await interaction.response.send_message("Invalid regex pattern.")
                return
                
        async with self.config.guild(interaction.guild).feeds() as feeds:
            if feed_name not in feeds:
                await interaction.response.send_message(f"Feed '{feed_name}' not found.")
                return
                
            feeds[feed_name].setdefault("filters", []).append({
                "type": type.value,
                "mode": mode.value,
                "value": value,
                "case_sensitive": case_sensitive
            })
        await interaction.response.send_message(f"Added {mode.value} {type.value} filter to '{feed_name}'.")

    @messagetorss.command(name="removefilter", description="Remove a filter from a feed by index")
    @discord_app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_removefilter(self, interaction: discord.Interaction, feed_name: str, filter_index: int):
        async with self.config.guild(interaction.guild).feeds() as feeds:
            if feed_name not in feeds:
                await interaction.response.send_message(f"Feed '{feed_name}' not found.")
                return
                
            filters = feeds[feed_name].get("filters", [])
            if 0 <= filter_index < len(filters):
                removed = filters.pop(filter_index)
                await interaction.response.send_message(f"Removed filter: {removed['type']} {removed['mode']} '{removed['value']}'")
            else:
                await interaction.response.send_message("Invalid filter index.")

    @messagetorss.command(name="listfilters", description="List filters for a feed or all feeds")
    @discord_app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_listfilters(self, interaction: discord.Interaction, feed_name: str = None):
        guild_data = await self.config.guild(interaction.guild).all()
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
        await interaction.response.send_message(msg)

    @messagetorss.command(name="addglobalfilter", description="Add a guild-wide filter")
    @discord_app_commands.choices(
        type=[
            discord_app_commands.Choice(name="keyword", value="keyword"),
            discord_app_commands.Choice(name="regex", value="regex"),
            discord_app_commands.Choice(name="user", value="user"),
            discord_app_commands.Choice(name="role", value="role"),
        ],
        mode=[
            discord_app_commands.Choice(name="whitelist", value="whitelist"),
            discord_app_commands.Choice(name="blacklist", value="blacklist"),
        ]
    )
    @discord_app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_addglobalfilter(self, interaction: discord.Interaction, type: discord_app_commands.Choice[str], mode: discord_app_commands.Choice[str], value: str, case_sensitive: bool = False):
        if type.value == "regex":
            try:
                re.compile(value)
            except re.error:
                await interaction.response.send_message("Invalid regex pattern.")
                return
                
        async with self.config.guild(interaction.guild).global_filters() as filters:
            filters.append({
                "type": type.value,
                "mode": mode.value,
                "value": value,
                "case_sensitive": case_sensitive
            })
        await interaction.response.send_message(f"Added global {mode.value} {type.value} filter.")

    @messagetorss.command(name="removeglobalfilter", description="Remove a guild-wide filter")
    @discord_app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_removeglobalfilter(self, interaction: discord.Interaction, filter_index: int):
        async with self.config.guild(interaction.guild).global_filters() as filters:
            if 0 <= filter_index < len(filters):
                removed = filters.pop(filter_index)
                await interaction.response.send_message(f"Removed global filter: {removed['type']} {removed['mode']} '{removed['value']}'")
            else:
                await interaction.response.send_message("Invalid filter index.")

    @messagetorss.command(name="toggle", description="Toggle the cog on/off for this guild")
    @discord_app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_toggle(self, interaction: discord.Interaction):
        current = await self.config.guild(interaction.guild).enabled()
        await self.config.guild(interaction.guild).enabled.set(not current)
        await interaction.response.send_message(f"MessageToRSS is now {'enabled' if not current else 'disabled'}.")

    @messagetorss.command(name="setincludebot", description="Whether to include bot messages")
    @discord_app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_setincludebot(self, interaction: discord.Interaction, enabled: bool):
        await self.config.guild(interaction.guild).include_bot.set(enabled)
        await interaction.response.send_message(f"Include bot messages set to {enabled}.")

    @messagetorss.command(name="setincludeembeds", description="Whether to include embed content from messages")
    @discord_app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_setincludeembeds(self, interaction: discord.Interaction, enabled: bool):
        await self.config.guild(interaction.guild).include_embeds.set(enabled)
        await interaction.response.send_message(f"Include embeds set to {enabled}.")

    @messagetorss.command(name="setmaxitems", description="Set max items per feed")
    @discord_app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_setmaxitems(self, interaction: discord.Interaction, max_items: int):
        if max_items <= 0:
            await interaction.response.send_message("Max items must be greater than 0.")
            return
        await self.config.guild(interaction.guild).max_items.set(max_items)
        await interaction.response.send_message(f"Max items per feed set to {max_items}.")

    @messagetorss.command(name="setstorage", description="Set feed storage mode")
    @discord_app_commands.choices(
        mode=[
            discord_app_commands.Choice(name="local", value="local"),
            discord_app_commands.Choice(name="remote", value="remote"),
        ]
    )
    @discord_app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_setstorage(self, interaction: discord.Interaction, mode: discord_app_commands.Choice[str]):
        await self.config.guild(interaction.guild).feed_storage.set(mode.value)
        await interaction.response.send_message(f"Feed storage mode set to {mode.value}.")

    @messagetorss.command(name="setremoteurl", description="Set remote URL for a feed")
    @discord_app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_setremoteurl(self, interaction: discord.Interaction, feed_name: str, url: str):
        async with self.config.guild(interaction.guild).remote_urls() as urls:
            urls[feed_name] = url
        await interaction.response.send_message(f"Remote URL for feed '{feed_name}' set.")

    @messagetorss.command(name="showconfig", description="Show current configuration")
    @discord_app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_showconfig(self, interaction: discord.Interaction):
        data = await self.config.guild(interaction.guild).all()
        msg = f"**Enabled:** {data['enabled']}\n"
        msg += f"**Storage:** {data['feed_storage']}\n"
        msg += f"**Max Items:** {data['max_items']}\n"
        msg += f"**Include Bot:** {data['include_bot']}\n"
        msg += f"**Include Embeds:** {data['include_embeds']}\n"
        await interaction.response.send_message(msg)

    @messagetorss.command(name="feedpath", description="Show the local file path for a feed")
    @discord_app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_feedpath(self, interaction: discord.Interaction, feed_name: str):
        feed_dir = cog_data_path(self) / "feeds"
        feed_file = feed_dir / f"{feed_name}.xml"
        await interaction.response.send_message(f"Local path: `{feed_file}`")

    @messagetorss.command(name="feedurl", description="Show the full HTTP URL for accessing a feed")
    @discord_app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_feedurl(self, interaction: discord.Interaction, feed_name: str):
        host = await self.config.http_host()
        port = await self.config.actual_port()
        url = f"http://{host}:{port}/feeds/{feed_name}"
        await interaction.response.send_message(f"Feed URL: {url}")

    @messagetorss.command(name="setport", description="Change the HTTP server port (restarts server)")
    @discord_app_commands.checks.has_permissions(manage_guild=True)
    @commands.is_owner()
    async def cmd_setport(self, interaction: discord.Interaction, port: int):
        await self.config.http_port.set(port)
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        self.bot.loop.create_task(self._start_http_server())
        await interaction.response.send_message(f"HTTP server restarting on base port {port}...")

    @messagetorss.command(name="serverstatus", description="Show HTTP server status")
    @discord_app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_serverstatus(self, interaction: discord.Interaction):
        host = await self.config.http_host()
        port = await self.config.actual_port()
        running = self._runner is not None
        
        feed_dir = cog_data_path(self) / "feeds"
        feed_count = 0
        if feed_dir.exists():
            feed_count = len([f for f in feed_dir.iterdir() if f.name.endswith(".xml")])
            
        msg = f"**Status:** {'Running' if running else 'Stopped'}\n"
        msg += f"**Host:** {host}\n"
        msg += f"**Port:** {port}\n"
        msg += f"**Feeds Served:** {feed_count}"
        await interaction.response.send_message(msg)
