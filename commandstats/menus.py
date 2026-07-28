import datetime
from typing import Any, Iterable, Optional

import discord
import tabulate
from redbot.core import commands
from redbot.core.utils.chat_formatting import box, escape, humanize_number


class StatsPaginator(discord.ui.View):
    """Button-based pagination view for command stats."""

    def __init__(
        self,
        source: "StatsSource",
        owner_id: int,
        timeout: float = 180,
    ):
        super().__init__(timeout=timeout)
        self.source = source
        self.owner_id = owner_id
        self.current_page = 0
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in (*getattr(interaction.client, "owner_ids", set()), self.owner_id):
            await interaction.response.send_message(
                "You don't have permission to use these controls.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        if self.message:
            with __import__("contextlib").suppress(discord.NotFound):
                await self.message.edit(view=None)

    def _update_buttons(self):
        max_pages = self.source.get_max_pages()
        self.first_page.disabled = self.current_page == 0 or max_pages <= 2
        self.prev_page.disabled = self.current_page == 0 or max_pages <= 1
        self.next_page.disabled = self.current_page >= max_pages - 1 or max_pages <= 1
        self.last_page.disabled = self.current_page >= max_pages - 1 or max_pages <= 2

    async def show_page(self, page: int, interaction: Optional[discord.Interaction] = None):
        if page < 0:
            page = self.source.get_max_pages() - 1
        elif page >= self.source.get_max_pages():
            page = 0
        self.current_page = page
        self._update_buttons()
        embed = await self.source.format_page(self, self.source.get_page(page))
        if interaction:
            await interaction.response.edit_message(embed=embed, view=self)
        elif self.message:
            await self.message.edit(embed=embed, view=self)

    @discord.ui.button(label="⏮", style=discord.ButtonStyle.secondary, row=0)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_page(0, interaction)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.primary, row=0)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_page(self.current_page - 1, interaction)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, row=0)
    async def stop_pages(self, interaction: discord.Interaction, button: discord.ui.Button):
        with __import__("contextlib").suppress(discord.NotFound):
            await self.message.delete()
        self.stop()

    @discord.ui.button(label="▶", style=discord.ButtonStyle.primary, row=0)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_page(self.current_page + 1, interaction)

    @discord.ui.button(label="⏭", style=discord.ButtonStyle.secondary, row=0)
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_page(self.source.get_max_pages() - 1, interaction)

    async def start(self, ctx):
        max_pages = self.source.get_max_pages()
        if max_pages <= 1:
            # Single page — no buttons needed
            embed = await self.source.format_page(self, self.source.get_page(0))
            self.clear_items()
            self.message = await ctx.send(embed=embed)
        else:
            self._update_buttons()
            embed = await self.source.format_page(self, self.source.get_page(self.current_page))
            self.message = await ctx.send(embed=embed, view=self)


class StatsSource:
    """Base page source for stats pagination."""

    def __init__(self, entries: Iterable, per_page: int = 1):
        self.entries = list(entries)
        self.per_page = per_page

    def get_max_pages(self) -> int:
        if not self.entries:
            return 1
        return (len(self.entries) + self.per_page - 1) // self.per_page

    def get_page(self, page_number: int):
        if page_number < 0 or page_number >= self.get_max_pages():
            return []
        start = page_number * self.per_page
        return self.entries[start : start + self.per_page]

    async def format_page(self, menu: StatsPaginator, data) -> discord.Embed:
        raise NotImplementedError


class EmbedFormat(StatsSource):
    """Formats command stats as a tabulated embed."""

    def __init__(self, entries: Iterable, title: str = None, stat_type: str = None,
                 ctx=None, timestamp: Optional[datetime.datetime] = None):
        super().__init__(entries, per_page=1)
        self.title = title
        self.stat_type = stat_type
        self.ctx = ctx
        self.timestamp = timestamp

    async def format_page(self, menu: StatsPaginator, data) -> discord.Embed:
        stats = list(data)
        embed = discord.Embed(
            title=self.title,
            colour=await self.ctx.embed_color(),
            description=box(
                tabulate.tabulate(stats, headers=[self.stat_type, "Times Used"]), lang="prolog"
            ),
        )
        if self.timestamp is not None:
            embed.set_footer(text="Recording commands since")
            embed.timestamp = self.timestamp
        else:
            embed.set_footer(
                text="Page {page}/{amount}".format(
                    page=menu.current_page + 1, amount=self.get_max_pages()
                )
            )
        return embed


class LeaderboardSource(StatsSource):
    """Formats guild leaderboard as a paginated embed."""

    def __init__(self, entries):
        super().__init__(entries, per_page=10)

    async def format_page(self, menu: StatsPaginator, entries) -> discord.Embed:
        bot = menu.message.guild.me if menu.message.guild else menu.message.channel.me
        ctx = None
        # Try to get embed color from the original context
        color = bot.color if hasattr(bot, "color") else discord.Color.blurple()

        position = (menu.current_page * self.per_page) + 1
        bal_len = len(humanize_number(entries[0][1])) if entries else 1
        pound_len = len(str(position + 9))
        header = "{pound:{pound_len}}{score:{bal_len}}{name:2}\n".format(
            pound="#",
            name=("Name"),
            score=("Score"),
            bal_len=bal_len + 6,
            pound_len=pound_len + 3,
        )
        msg = ""
        for i, data in enumerate(entries, start=position):
            try:
                server = bot.get_guild(int(data[0])).name
            except AttributeError:
                server = "<unknown server>"
            name = escape(server, formatting=True)

            balance = data[1]
            balance = humanize_number(balance)
            msg += f"{humanize_number(i)}. {balance: <{bal_len + 5}} {name}\n"

        page = discord.Embed(
            title="Guild Command Leaderboard.",
            color=color,
            description="{}\n{} ".format(box(header, lang="prolog"), box(msg, lang="md")),
        )
        page.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")
        return page


# Compatibility wrapper — lets commandstats.py use the same interface
class GenericMenu:
    """Compatibility wrapper that replaces the old reaction-based MenuPages."""

    def __init__(
        self,
        source,
        cog: Optional[commands.Cog] = None,
        title: Optional[str] = None,
        _type: Optional[str] = None,
        ctx=None,
        timestamp: Optional[datetime.datetime] = None,
        **kwargs: Any,
    ):
        # Wrap the old source in the new EmbedFormat if it's a raw list
        if isinstance(source, EmbedFormat):
            source.title = title or source.title
            source.stat_type = _type or source.stat_type
            source.ctx = ctx or source.ctx
            source.timestamp = timestamp or source.timestamp
            self.source = source
        elif isinstance(source, LeaderboardSource):
            self.source = source
        else:
            # Fallback: wrap raw entries
            self.source = EmbedFormat(
                entries=source.entries if hasattr(source, "entries") else source,
                title=title,
                stat_type=_type,
                ctx=ctx,
                timestamp=timestamp,
            )
        self.ctx = ctx

    async def start(self, ctx=None, wait=False, **kwargs):
        ctx = ctx or self.ctx
        if ctx is None:
            raise ValueError("No context provided to GenericMenu.start()")

        # Get the owner ID for interaction checks
        owner_id = ctx.author.id

        # For LeaderboardSource, we need the bot for guild lookups
        if isinstance(self.source, LeaderboardSource):
            view = StatsPaginator(self.source, owner_id=owner_id)
            # Store bot reference for guild lookups
            view.source = self.source
            # Override format to use bot from ctx
            original_format = self.source.format_page

            async def format_with_ctx(menu, entries):
                bot = ctx.bot
                position = (menu.current_page * self.source.per_page) + 1
                bal_len = len(humanize_number(entries[0][1])) if entries else 1
                pound_len = len(str(position + 9))
                header = "{pound:{pound_len}}{score:{bal_len}}{name:2}\n".format(
                    pound="#",
                    name=("Name"),
                    score=("Score"),
                    bal_len=bal_len + 6,
                    pound_len=pound_len + 3,
                )
                msg = ""
                for i, data in enumerate(entries, start=position):
                    try:
                        server = bot.get_guild(int(data[0])).name
                    except (AttributeError, ValueError):
                        server = "<unknown server>"
                    name = escape(server, formatting=True)
                    balance = humanize_number(data[1])
                    msg += f"{humanize_number(i)}. {balance: <{bal_len + 5}} {name}\n"

                color = await ctx.embed_color()
                page = discord.Embed(
                    title="Guild Command Leaderboard.",
                    color=color,
                    description="{}\n{} ".format(box(header, lang="prolog"), box(msg, lang="md")),
                )
                page.set_footer(text=f"Page {menu.current_page + 1}/{self.source.get_max_pages()}")
                return page

            self.source.format_page = format_with_ctx
        else:
            view = StatsPaginator(self.source, owner_id=owner_id)

        await view.start(ctx)
        return view