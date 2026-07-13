from .database import BaseDB

class LevelerAPI:
    """
    Public API for the Leveler cog.
    Other cogs can access this via:
        leveler = bot.get_cog("Leveler")
        if leveler:
            await leveler.api.get_user_level(guild_id, user_id)
    """

    def __init__(self, db: BaseDB):
        self.db = db

    async def get_user_data(self, guild_id: int, user_id: int) -> dict:
        """
        Retrieves user leveling data.
        Returns a dict: {'xp': int, 'level': int, 'background_id': str, ...}
        """
        return await self.db.get_user(guild_id, user_id)

    async def get_user_level(self, guild_id: int, user_id: int) -> int:
        """Helper to get just the user's level."""
        data = await self.get_user_data(guild_id, user_id)
        return data["level"]

    async def get_user_xp(self, guild_id: int, user_id: int) -> int:
        """Helper to get just the user's xp."""
        data = await self.get_user_data(guild_id, user_id)
        return data["xp"]

    async def add_user_xp(self, guild_id: int, user_id: int, amount: int) -> tuple[int, int]:
        """
        Adds XP to a user.
        Returns a tuple of (new_xp, new_level).
        """
        return await self.db.add_user_xp(guild_id, user_id, amount)

    async def get_leaderboard(self, guild_id: int, limit: int = 10, offset: int = 0) -> list:
        """
        Gets the leaderboard for a specific guild.
        Returns a list of tuples: [(user_id, xp, level), ...]
        """
        return await self.db.get_leaderboard(guild_id, limit, offset)

    async def get_global_leaderboard(self, limit: int = 10, offset: int = 0) -> list:
        """
        Gets the global leaderboard across all guilds.
        Returns a list of tuples: [(user_id, total_xp, max_level), ...]
        """
        return await self.db.get_global_leaderboard(limit, offset)
