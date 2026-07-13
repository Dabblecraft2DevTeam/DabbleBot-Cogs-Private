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
            db_path = os.path.join(self.bot.config_dir, "leveler.db")
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
            
        guild = message.guild
        user = message.author
        
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
        gained_xp = random.randint(xp_min, xp_max)
        
        # Use our DB to add XP and get new level
        old_data = await self.db.get_user(guild.id, user.id)
        old_level = old_data["level"]
        
        new_xp, new_level = await self.db.add_user_xp(guild.id, user.id, gained_xp)
        
        # Check for level up
        if new_level > old_level:
            await self.handle_level_up(guild, user, new_level)

    async def handle_level_up(self, guild: discord.Guild, user: discord.Member, new_level: int):
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
                
            await channel.send(content=msg, file=file)
