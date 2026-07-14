import discord
import asyncio
import re
from redbot.core import commands, app_commands, bank
from .image_gen import generate_profile_card
from .ui import LeaderboardPaginationView, LevelShopView

URL_REGEX = re.compile(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')

class CommandsMixin:
    """Mixin for Leveler commands."""

    @commands.hybrid_command(name="profile", description="Shows your or another user's leveling profile.")
    async def profile(self, ctx: commands.Context, user: discord.Member = None):
        if not await self.config.guild(ctx.guild).is_enabled():
            return await ctx.send("The leveling system is currently disabled on this server.")
            
        await ctx.defer()
        user = user or ctx.author
        if user.bot:
            return await ctx.send("Bots don't have levels!")
        
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
                
        # Fetch all unlocked prestige badge URLs
        prestige_urls = []
        milestones = await self.config.guild(ctx.guild).prestige_milestones()
        for lvl_str in sorted([int(k) for k in milestones.keys()]):
            if lvl_str <= user_data["level"]:
                data = milestones[str(lvl_str)]
                if isinstance(data, dict) and data.get("image_url"):
                    prestige_urls.append(data["image_url"])
                    
        # Append legacy badges that can no longer be earned
        legacy_badges = await self.config.member(user).legacy_badges()
        prestige_urls.extend(legacy_badges)

        img_bytes = await generate_profile_card(
            user.display_name,
            avatar_bytes,
            user_data["xp"],
            user_data["level"],
            rank,
            user_data["title_color"],
            user_data["bar_color"],
            user_data.get("bio", ""),
            user_data.get("background_id", "default"),
            prestige_urls
        )
        file = discord.File(img_bytes, filename="profile.png")
        await ctx.send(file=file)

    @commands.hybrid_command(name="bio", description="Set your profile bio.")
    @app_commands.describe(text="The bio text (max 100 characters).")
    async def bio(self, ctx: commands.Context, *, text: str):
        if not await self.config.guild(ctx.guild).is_enabled():
            return await ctx.send("The leveling system is currently disabled on this server.")
            
        # Sanitize URLs
        clean_text = URL_REGEX.sub("[LINK REMOVED]", text)
        if len(clean_text) > 100:
            clean_text = clean_text[:97] + "..."
            
        await self.db.update_user_cosmetics(ctx.guild.id, ctx.author.id, bio=clean_text)
        await ctx.send("Your bio has been updated.")

    @commands.hybrid_command(name="top", description="Shows the top users in the server.")
    async def top(self, ctx: commands.Context):
        if not await self.config.guild(ctx.guild).is_enabled():
            return await ctx.send("The leveling system is currently disabled on this server.")
            
        await ctx.defer()
        leaderboard = await self.db.get_leaderboard(ctx.guild.id, limit=100)
        
        if not leaderboard:
            return await ctx.send("No one has any XP yet!")
            
        milestones = await self.config.guild(ctx.guild).prestige_milestones()
        embeds = []
        for i in range(0, len(leaderboard), 10):
            chunk = leaderboard[i:i+10]
            embed = discord.Embed(title=f"Leaderboard for {ctx.guild.name}", color=await ctx.embed_color())
            description = ""
            for j, row in enumerate(chunk):
                user_id, xp, level, prestige_lvl = row
                member = ctx.guild.get_member(user_id)
                name = member.display_name if member else f"User {user_id}"
                
                badge = ""
                if prestige_lvl > 0 and str(prestige_lvl) in milestones:
                    badge_data = milestones[str(prestige_lvl)]
                    if isinstance(badge_data, dict):
                        badge = f" {badge_data.get('emoji', '')}"
                    else:
                        badge = f" {badge_data}"
                    
                description += f"**{i + j + 1}.** {name}{badge} - Level {level} ({xp} XP)\n"
            embed.description = description
            embeds.append(embed)
            
        if len(embeds) == 1:
            await ctx.send(embed=embeds[0])
        else:
            view = LeaderboardPaginationView(embeds)
            await ctx.send(embed=embeds[0], view=view)

    @commands.hybrid_command(name="levelshop", description="Buy cosmetics for your profile card.")
    async def levelshop(self, ctx: commands.Context, shop_type: str = "colors"):
        """Buy colors or backgrounds. Usage: [p]levelshop colors OR [p]levelshop backgrounds"""
        if not await self.config.guild(ctx.guild).is_enabled():
            return await ctx.send("The leveling system is currently disabled on this server.")
            
        embed = discord.Embed(
            title=f"Leveler Cosmetics Shop - {shop_type.capitalize()}",
            description="Use the dropdown below to purchase custom cosmetics using server credits.",
            color=await ctx.embed_color()
        )
        
        item_type = "background_id" if shop_type.lower().startswith("bg") or shop_type.lower() == "backgrounds" else "bar_color"
        options = await self.config.guild(ctx.guild).shop_backgrounds() if item_type == "background_id" else await self.config.guild(ctx.guild).shop_colors()
        
        view = LevelShopView(self, ctx.author, self.db, item_type=item_type, config_options=options)
        await ctx.send(embed=embed, view=view)

    @commands.hybrid_command(name="resetrank", description="Reset your rank and start over (costs credits if enabled).")
    async def resetrank(self, ctx: commands.Context):
        settings = await self.config.guild(ctx.guild).all()
        if not settings["is_enabled"]:
            return await ctx.send("The leveling system is currently disabled on this server.")
            
        if not settings.get("rank_reset_enabled"):
            return await ctx.send("Rank resets are not enabled on this server.")
            
        max_level = settings.get("max_level", 0)
        user_data = await self.db.get_user(ctx.guild.id, ctx.author.id)
        
        if max_level > 0 and user_data["level"] < max_level:
            return await ctx.send(f"You must reach the maximum level ({max_level}) to reset your rank!")
            
        price = settings.get("rank_reset_price", 0)
        
        if price > 0:
            if not await bank.can_spend(ctx.author, price):
                return await ctx.send(f"You don't have enough credits! A rank reset costs {price} credits.")
            await bank.withdraw_credits(ctx.author, price)
            
        # Reset XP and Level in DB
        # Since add_user_xp adds xp, we just manually update the DB to 0 for this user.
        # update_user_cosmetics isn't meant for XP.
        if hasattr(self.db, "conn"): # sqlite
            await self.db.conn.execute("UPDATE users SET xp = 0, level = 0 WHERE guild_id = ? AND user_id = ?", (ctx.guild.id, ctx.author.id))
            await self.db.conn.commit()
        else: # mysql
            async with self.db.pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("UPDATE users SET xp = 0, level = 0 WHERE guild_id = %s AND user_id = %s", (ctx.guild.id, ctx.author.id))
                    
        msg = f"🎉 Your rank has been successfully reset!"
        if price > 0:
            msg += f" (Cost: {price} credits)"
        await ctx.send(msg)

    @commands.group(name="levelset")
    @commands.admin_or_permissions(manage_guild=True)
    async def levelset(self, ctx: commands.Context):
        """Configure the leveling system."""
        pass
        
    @levelset.command(name="toggle")
    async def levelset_toggle(self, ctx: commands.Context):
        """Enable or disable the leveling system in this server."""
        current = await self.config.guild(ctx.guild).is_enabled()
        await self.config.guild(ctx.guild).is_enabled.set(not current)
        state = "enabled" if not current else "disabled"
        await ctx.send(f"The leveling system is now **{state}**.")

    @levelset.command(name="guide")
    async def levelset_guide(self, ctx: commands.Context):
        """Displays a comprehensive guide on how to configure and use the Leveler cog."""
        embed = discord.Embed(
            title="📚 DabbleLeveler Admin Guide",
            description="Welcome to the Leveler cog! Here is everything you need to know to configure and manage the leveling system for your server.",
            color=await ctx.embed_color()
        )
        
        embed.add_field(
            name="1. Getting Started",
            value="The cog is **disabled by default**. To start tracking XP, an admin must run `[p]levelset toggle`. Users gain XP by sending messages (subject to a cooldown).",
            inline=False
        )
        
        embed.add_field(
            name="2. XP & Algorithms",
            value=(
                "• `[p]levelset range <min> <max>`: Set the random XP amount granted per message.\n"
                "• `[p]levelset cooldown <seconds>`: Set how often users can gain XP from messages.\n"
                "• `[p]levelset algorithm <mee6|stevy|linear>`: Change how XP scales per level. Mee6 is exponential (harder), Stevy is extremely exponential, and Linear is a flat curve."
            ),
            inline=False
        )
        
        embed.add_field(
            name="3. Level Up Rewards",
            value=(
                "• `[p]levelset channel <#channel>`: Set where level-up messages are sent.\n"
                "• `[p]levelset reward <amount>`: Set how many economy credits users earn when they level up."
            ),
            inline=False
        )
        
        embed.add_field(
            name="4. The Cosmetics Shop",
            value=(
                "Users can spend their credits in `[p]levelshop colors` and `[p]levelshop backgrounds`.\n"
                "• `[p]levelset shop addcolor <name> <hex> <price>`: Add a color to the shop.\n"
                "• `[p]levelset shop addbg <name> <image_url> <price>`: Add a background to the shop."
            ),
            inline=False
        )
        
        embed.add_field(
            name="5. Prestige Badges",
            value=(
                "Reward dedicated users with prestige badges!\n"
                "• `[p]levelset prestige add <level> <emoji> <image_url>`: Adds a badge for reaching a level. The emoji appears in `[p]top`, and the image appears on their `[p]profile` card.\n"
                "• `[p]levelset prestige edit <level> <emoji> <image_url>`: Edit the emoji and image of an existing badge.\n"
                "• `[p]levelset prestige remove <level>`: Remove a badge. You will be asked if you want to also revoke it from users who already earned it, or let them keep it as a legacy badge."
            ),
            inline=False
        )
        
        embed.add_field(
            name="6. Moderation & Features",
            value=(
                "• `[p]levelset addxp <user> <amount>`: Manually grant XP to a user.\n"
                "• `[p]levelset resetbio <user>`: Wipe an inappropriate user bio.\n"
                "• `[p]levelset maxlevel <level>`: Set the maximum level users can reach.\n"
                "• `[p]levelset rankreset <True/False>`: Enable the `[p]resetrank` command for max level users.\n"
                "• `[p]levelset rankresetprice <price>`: Set the credit cost to reset rank."
            ),
            inline=False
        )

        await ctx.send(embed=embed)

    @levelset.command(name="maxlevel")
    async def levelset_maxlevel(self, ctx: commands.Context, level: int):
        """Set the maximum level a user can reach (0 for infinite)."""
        await self.config.guild(ctx.guild).max_level.set(level)
        await ctx.send(f"Max level set to **{'Infinite' if level <= 0 else level}**.")

    @levelset.command(name="rankreset")
    async def levelset_rankreset(self, ctx: commands.Context, toggle: bool):
        """Enable or disable the [p]resetrank command for max level users."""
        await self.config.guild(ctx.guild).rank_reset_enabled.set(toggle)
        await ctx.send(f"Rank reset command has been **{'Enabled' if toggle else 'Disabled'}**.")

    @levelset.command(name="rankresetprice")
    async def levelset_rankresetprice(self, ctx: commands.Context, price: int):
        """Set the credit cost to use the [p]resetrank command."""
        await self.config.guild(ctx.guild).rank_reset_price.set(price)
        await ctx.send(f"Rank reset price set to **{price} credits**.")

    @levelset.command(name="channel")
    async def levelset_channel(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """Sets the channel for level-up messages. Leave blank to disable."""
        if channel:
            await self.config.guild(ctx.guild).level_up_channel.set(channel.id)
            await ctx.send(f"Level-up messages will be sent to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).level_up_channel.set(None)
            await ctx.send("Level-up messages disabled.")

    @levelset.command(name="range")
    async def levelset_range(self, ctx: commands.Context, min_xp: int, max_xp: int):
        """Set the random XP amount granted per message."""
        if min_xp > max_xp:
            return await ctx.send("Min XP cannot be greater than Max XP!")
        await self.config.guild(ctx.guild).xp_min.set(min_xp)
        await self.config.guild(ctx.guild).xp_max.set(max_xp)
        await ctx.send(f"XP range set to **{min_xp} - {max_xp}**.")

    @levelset.command(name="cooldown")
    async def levelset_cooldown(self, ctx: commands.Context, seconds: int):
        """Set how often users can gain XP from messages."""
        await self.config.guild(ctx.guild).message_cooldown.set(seconds)
        await ctx.send(f"XP cooldown set to **{seconds} seconds**.")

    @levelset.command(name="reward")
    async def levelset_reward(self, ctx: commands.Context, amount: int):
        """Set how many economy credits users earn when they level up."""
        await self.config.guild(ctx.guild).level_up_reward.set(amount)
        await ctx.send(f"Level up reward set to **{amount} credits**.")

    @levelset.command(name="show")
    async def levelset_show(self, ctx: commands.Context):
        """Shows the current leveling configuration for this server."""
        guild = ctx.guild
        settings = await self.config.guild(guild).all()
        
        embed = discord.Embed(title=f"Leveler Config for {guild.name}", color=await ctx.embed_color())
        embed.add_field(name="XP Cooldown", value=f"{settings['message_cooldown']}s", inline=True)
        
        chan = guild.get_channel(settings['level_up_channel']) if settings['level_up_channel'] else None
        embed.add_field(name="Level Up Channel", value=chan.mention if chan else "Disabled", inline=True)
        
        embed.add_field(name="Level Up Reward", value=f"{settings['level_up_reward']} credits", inline=True)
        embed.add_field(name="XP Min", value=str(settings['xp_min']))
        embed.add_field(name="XP Max", value=str(settings['xp_max']))
        embed.add_field(name="Algorithm", value=str(settings['algorithm']).capitalize())
        embed.add_field(name="Max Level", value=str(settings['max_level']) if settings.get('max_level') else "Infinite")
        embed.add_field(name="Rank Reset", value="Enabled" if settings.get('rank_reset_enabled') else "Disabled")
        if settings.get('rank_reset_enabled'):
            embed.add_field(name="Reset Price", value=f"{settings.get('rank_reset_price')} credits")
        embed.add_field(name="Status", value="Enabled" if settings['is_enabled'] else "Disabled")
        
        await ctx.send(embed=embed)

    @levelset.command(name="algorithm")
    async def levelset_algorithm(self, ctx: commands.Context, algorithm: str):
        """Sets the algorithm used to calculate levels. 
        Options: mee6, linear, stevy
        """
        algorithm = algorithm.lower()
        if algorithm not in ["mee6", "linear", "stevy"]:
            return await ctx.send("Invalid algorithm. Choose from: mee6, linear, stevy")
            
        await self.config.guild(ctx.guild).algorithm.set(algorithm)
        await ctx.send(f"Leveling algorithm set to **{algorithm.capitalize()}**.")

    @levelset.command(name="resetbio")
    async def levelset_resetbio(self, ctx: commands.Context, user: discord.Member):
        """Resets a user's bio."""
        await self.db.update_user_cosmetics(ctx.guild.id, user.id, bio="")
        await ctx.send(f"{user.display_name}'s bio has been reset.")

    @levelset.command(name="addxp")
    async def levelset_addxp(self, ctx: commands.Context, user: discord.Member, amount: int):
        """Add XP to a user manually."""
        algorithm = await self.config.guild(ctx.guild).algorithm()
        max_level = await self.config.guild(ctx.guild).max_level()
        
        old_data = await self.db.get_user(ctx.guild.id, user.id)
        old_level = old_data["level"]
        
        new_xp, new_level = await self.db.add_user_xp(ctx.guild.id, user.id, amount, algorithm, max_level)
        await ctx.send(f"Added {amount} XP to {user.display_name}. They are now at {new_xp} XP (Level {new_level}).")
        
        if new_level > old_level:
            await self.handle_level_up(ctx.guild, user, new_level, old_level)

    @levelset.group(name="shop")
    async def levelset_shop(self, ctx: commands.Context):
        """Configure the leveling shop."""
        pass

    @levelset_shop.command(name="addcolor")
    async def shop_addcolor(self, ctx: commands.Context, name: str, hex_code: str, price: int):
        """Add a color to the shop. Example: [p]levelset shop addcolor Red #FF0000 500"""
        async with self.config.guild(ctx.guild).shop_colors() as colors:
            colors.append({"label": name, "value": hex_code, "price": price})
        await ctx.send(f"Added color '{name}' ({hex_code}) for {price} credits.")

    @levelset_shop.command(name="addbg")
    async def shop_addbg(self, ctx: commands.Context, name: str, url: str, price: int):
        """Add a background URL to the shop."""
        async with self.config.guild(ctx.guild).shop_backgrounds() as bgs:
            bgs.append({"label": name, "value": url, "price": price})
        await ctx.send(f"Added background '{name}' for {price} credits.")

    @levelset.group(name="prestige")
    async def levelset_prestige(self, ctx: commands.Context):
        """Configure prestige badges."""
        pass

    @levelset_prestige.command(name="add")
    async def prestige_add(self, ctx: commands.Context, level: int, emoji: str, image_url: str):
        """Add a prestige badge. Usage: [p]levelset prestige add <level> <emoji> <image_url>"""
        async with self.config.guild(ctx.guild).prestige_milestones() as milestones:
            milestones[str(level)] = {"emoji": emoji, "image_url": image_url}
        await ctx.send(f"Added prestige badge for reaching level {level}.")

    @levelset_prestige.command(name="edit")
    async def prestige_edit(self, ctx: commands.Context, level: int, emoji: str, image_url: str):
        """Edit an existing prestige badge. Usage: [p]levelset prestige edit <level> <emoji> <image_url>"""
        async with self.config.guild(ctx.guild).prestige_milestones() as milestones:
            if str(level) not in milestones:
                return await ctx.send(f"No prestige badge found for level {level}. Use `add` instead.")
            milestones[str(level)] = {"emoji": emoji, "image_url": image_url}
        await ctx.send(f"Edited prestige badge for level {level}.")

    @levelset_prestige.command(name="remove")
    async def prestige_remove(self, ctx: commands.Context, level: int):
        """Remove a prestige badge."""
        async with self.config.guild(ctx.guild).prestige_milestones() as milestones:
            if str(level) not in milestones:
                return await ctx.send(f"No prestige badge found for level {level}.")
                
            badge_url = milestones[str(level)].get("image_url")
            
            await ctx.send(f"Do you want to remove the level {level} badge from users who already earned it? (yes/no)")
            try:
                msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=30.0)
                if msg.content.lower() in ("yes", "y"):
                    del milestones[str(level)]
                    await ctx.send(f"Badge for level {level} removed globally.")
                elif msg.content.lower() in ("no", "n"):
                    # Add to legacy badges for all users who have it
                    users = await self.db.get_leaderboard(ctx.guild.id, limit=999999)
                    count = 0
                    for u in users:
                        user_id = u[0]
                        u_level = u[2]
                        if u_level >= level:
                            async with self.config.member_from_ids(ctx.guild.id, user_id).legacy_badges() as lb:
                                if badge_url and badge_url not in lb:
                                    lb.append(badge_url)
                            count += 1
                    
                    del milestones[str(level)]
                    await ctx.send(f"Badge for level {level} removed from config, but kept for {count} legacy users.")
                else:
                    return await ctx.send("Invalid response. Cancelled.")
            except asyncio.TimeoutError:
                return await ctx.send("Prompt timed out. Cancelled.")

    @commands.group(name="levelerdb")
    @commands.is_owner()
    async def levelerdb(self, ctx: commands.Context):
        """Global configuration for the Leveler database (Bot Owner Only)."""
        pass

    @levelerdb.command(name="toggle")
    async def levelerdb_toggle(self, ctx: commands.Context):
        """Toggles between SQLite and MySQL. Requires a cog reload to take effect."""
        current = await self.config.use_mysql()
        await self.config.use_mysql.set(not current)
        new_state = "MySQL" if not current else "SQLite"
        await ctx.send(f"Database backend switched to **{new_state}**. Please reload the cog (`[p]reload leveler`) for changes to apply.")

    @levelerdb.command(name="setup")
    async def levelerdb_setup(self, ctx: commands.Context, host: str, user: str, password: str, db: str, port: int = 3306):
        """Sets the MySQL database credentials. Requires a cog reload to take effect."""
        await self.config.mysql_host.set(host)
        await self.config.mysql_user.set(user)
        await self.config.mysql_password.set(password)
        await self.config.mysql_db.set(db)
        await self.config.mysql_port.set(port)
        await ctx.send("MySQL configuration saved. Please reload the cog to apply.")
