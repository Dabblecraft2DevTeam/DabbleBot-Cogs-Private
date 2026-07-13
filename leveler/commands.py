import discord
from redbot.core import commands, app_commands
from .image_gen import generate_profile_card
from .ui import LeaderboardPaginationView, LevelShopView

class CommandsMixin:
    """Mixin for Leveler commands."""

    @commands.hybrid_command(name="profile", description="Shows the leveling profile of a user.")
    @app_commands.describe(user="The user to view.")
    async def profile(self, ctx: commands.Context, user: discord.Member = None):
        user = user or ctx.author
        if user.bot:
            return await ctx.send("Bots don't have levels!")

        await ctx.defer()
        
        # Get data
        db = self.db
        user_data = await db.get_user(ctx.guild.id, user.id)
        
        # Determine rank
        leaderboard = await db.get_leaderboard(ctx.guild.id, limit=1000)
        rank = next((i + 1 for i, row in enumerate(leaderboard) if row[0] == user.id), 0)
        
        # Generate image
        avatar_bytes = None
        if user.display_avatar:
            try:
                avatar_bytes = await user.display_avatar.with_format("png").with_size(256).read()
            except:
                pass
                
        img_bytes = await generate_profile_card(
            user.display_name,
            avatar_bytes,
            user_data["xp"],
            user_data["level"],
            rank,
            user_data["title_color"],
            user_data["bar_color"]
        )
        
        file = discord.File(img_bytes, filename="profile.png")
        await ctx.send(file=file)

    @commands.hybrid_command(name="top", description="Shows the top users in the server.")
    async def top(self, ctx: commands.Context):
        await ctx.defer()
        leaderboard = await self.db.get_leaderboard(ctx.guild.id, limit=100)
        
        if not leaderboard:
            return await ctx.send("No one has any XP yet!")
            
        embeds = []
        for i in range(0, len(leaderboard), 10):
            chunk = leaderboard[i:i+10]
            embed = discord.Embed(title=f"Leaderboard for {ctx.guild.name}", color=await ctx.embed_color())
            description = ""
            for j, row in enumerate(chunk):
                user_id, xp, level = row
                member = ctx.guild.get_member(user_id)
                name = member.display_name if member else f"User {user_id}"
                description += f"**{i + j + 1}.** {name} - Level {level} ({xp} XP)\n"
            embed.description = description
            embeds.append(embed)
            
        if len(embeds) == 1:
            await ctx.send(embed=embeds[0])
        else:
            view = LeaderboardPaginationView(embeds)
            await ctx.send(embed=embeds[0], view=view)

    @commands.hybrid_command(name="levelshop", description="Buy cosmetics for your profile card.")
    async def levelshop(self, ctx: commands.Context):
        embed = discord.Embed(
            title="Leveler Cosmetics Shop",
            description="Use the dropdown below to purchase custom colors for your profile card using server credits.",
            color=await ctx.embed_color()
        )
        view = LevelShopView(self, ctx.author, self.db, item_type="bar_color")
        await ctx.send(embed=embed, view=view)

    @commands.group(name="levelset")
    @commands.admin_or_permissions(manage_guild=True)
    async def levelset(self, ctx: commands.Context):
        """Configuration for the Leveler cog."""
        pass
        
    @levelset.command(name="channel")
    async def levelset_channel(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """Sets the channel for level-up messages. Leave blank to disable."""
        if channel:
            await self.config.guild(ctx.guild).level_up_channel.set(channel.id)
            await ctx.send(f"Level up messages will now be sent in {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).level_up_channel.set(None)
            await ctx.send("Level up messages have been disabled.")

    @levelset.command(name="cooldown")
    async def levelset_cooldown(self, ctx: commands.Context, seconds: int):
        """Sets the cooldown between XP gains (in seconds). Default is 60."""
        await self.config.guild(ctx.guild).message_cooldown.set(seconds)
        await ctx.send(f"XP cooldown set to {seconds} seconds.")

    @levelset.command(name="economy")
    async def levelset_economy(self, ctx: commands.Context, amount: int):
        """Sets the flat credit reward given on level up."""
        await self.config.guild(ctx.guild).level_up_reward.set(amount)
        await ctx.send(f"Users will now receive {amount} credits when they level up.")
