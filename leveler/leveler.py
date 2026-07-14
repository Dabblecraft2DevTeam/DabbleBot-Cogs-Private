import discord
from redbot.core import commands, Config, bank
from .database import SQLiteDB, MySQLDB
from .api import LevelerAPI
from .commands import CommandsMixin
from .image_gen import generate_levelup_card
import time
import os
import random

class Leveler(CommandsMixin, commands.Cog):
    """A highly customizable leveling cog with images and economy integration."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9988776655, force_registration=True)
        
        # Guild settings
        default_guild = {
            "message_cooldown": 60,
            "level_up_channel": None,
            "level_up_reward": 100,
            "xp_min": 15,
            "xp_max": 25,
            "algorithm": "mee6",
            "is_enabled": False,
            "max_level": 0,
            "rank_reset_enabled": False,
            "rank_reset_price": 10000,
            "shop_colors": [
                {"label": "Red", "value": "#FF0000", "price": 500},
                {"label": "Blue", "value": "#0000FF", "price": 500},
                {"label": "Green", "value": "#00FF00", "price": 500},
                {"label": "Gold", "value": "#FFD700", "price": 1000},
                {"label": "Purple", "value": "#800080", "price": 750}
            ],
            "shop_backgrounds": [
                {"label": "Default", "value": "default", "price": 0}
            ],
            "prestige_milestones": {}
        }
        
        # Global settings for database choice
        default_global = {
            "use_mysql": False,
            "mysql_host": "localhost",
            "mysql_user": "root",
            "mysql_password": "",
            "mysql_db": "redbot_leveler",
            "mysql_port": 3306
        }
        
        self.config.register_guild(**default_guild)
        self.config.register_global(**default_global)
        
        self.db = None
        self.api = None
        
        # In-memory cooldown tracker: {guild_id: {user_id: last_msg_time}}
        self._cooldowns = {}
        
    async def cog_load(self):
        """Called when the cog is loaded."""
        use_mysql = await self.config.use_mysql()
        if use_mysql:
            db_config = {
                "host": await self.config.mysql_host(),
                "user": await self.config.mysql_user(),
                "password": await self.config.mysql_password(),
                "db": await self.config.mysql_db(),
                "port": await self.config.mysql_port(),
            }
            self.db = MySQLDB(**db_config)
        else:
            from redbot.core import data_manager
            db_path = os.path.join(data_manager.cog_data_path(self), "leveler.db")
            self.db = SQLiteDB(db_path)
            
        await self.db.connect()
        self.api = LevelerAPI(self.db)
        
    async def cog_unload(self):
        """Called when the cog is unloaded."""
        if self.db:
            await self.db.close()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
            
        is_enabled = await self.config.guild(message.guild).is_enabled()
        if not is_enabled:
            return
            
        guild = message.guild
        user = message.author
        
        # Ignore commands
        ctx = await self.bot.get_context(message)
        if ctx.valid:
            return
        
        # Check cooldown
        now = time.time()
        cooldown = await self.config.guild(guild).message_cooldown()
        
        if guild.id not in self._cooldowns:
            self._cooldowns[guild.id] = {}
            
        last_msg = self._cooldowns[guild.id].get(user.id, 0)
        if now - last_msg < cooldown:
            return
            
        # Add XP
        self._cooldowns[guild.id][user.id] = now
        xp_min = await self.config.guild(guild).xp_min()
        xp_max = await self.config.guild(guild).xp_max()
        xp_amount = random.randint(xp_min, xp_max)
        
        # Use our DB to add XP and get new level
        old_data = await self.db.get_user(guild.id, user.id)
        old_level = old_data["level"]
        
        algorithm = await self.config.guild(guild).algorithm()
        max_level = await self.config.guild(guild).max_level()
        
        # Add XP
        new_xp, new_level = await self.db.add_user_xp(guild.id, user.id, xp_amount, algorithm, max_level)
        
        # Check for level up
        if new_level > old_level:
            await self.handle_level_up(guild, user, new_level, old_level)

    async def handle_level_up(self, guild: discord.Guild, user: discord.Member, new_level: int, old_level: int = 0):
        # 1. Economy reward
        reward = await self.config.guild(guild).level_up_reward()
        if reward > 0:
            try:
                await bank.deposit_credits(user, reward)
            except Exception:
                pass # Economy might be disabled or max balance reached
                
        # 2. Send image notification
        channel_id = await self.config.guild(guild).level_up_channel()
        channel = guild.get_channel(channel_id) if channel_id else None
        
        if channel:
            # Generate level up card
            avatar_bytes = None
            if user.display_avatar:
                try:
                    avatar_bytes = await user.display_avatar.with_format("png").with_size(128).read()
                except Exception:
                    pass
                    
            img_bytes = await generate_levelup_card(user.display_name, avatar_bytes, new_level)
            file = discord.File(img_bytes, filename="levelup.png")
            
            msg = f"Congratulations {user.mention}!"
            if reward > 0:
                msg += f" You earned **{reward}** credits!"
                
            # Prestige Check
            milestones = await self.config.guild(guild).prestige_milestones()
            highest_milestone = None
            
            # Check all levels we might have skipped over
            for lvl in range(old_level + 1, new_level + 1):
                if str(lvl) in milestones:
                    highest_milestone = str(lvl)
            
            if highest_milestone is not None:
                await self.db.update_user_cosmetics(guild.id, user.id, prestige=int(highest_milestone))
                badge_data = milestones[highest_milestone]
                if isinstance(badge_data, dict):
                    badge_emoji = badge_data.get("emoji", "")
                    msg += f" You also earned a prestige badge {badge_emoji}!"
                else:
                    msg += f" You also earned a prestige badge {badge_data}!"
                
            await channel.send(content=msg, file=file)
