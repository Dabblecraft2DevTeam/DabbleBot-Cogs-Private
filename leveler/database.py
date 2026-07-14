import abc
import aiosqlite
import logging

log = logging.getLogger("red.dabblecogs.leveler.database")

class BaseDB(abc.ABC):
    @abc.abstractmethod
    async def connect(self):
        pass

    @abc.abstractmethod
    async def close(self):
        pass

    @abc.abstractmethod
    async def get_user(self, guild_id: int, user_id: int):
        pass

    @abc.abstractmethod
    async def add_user_xp(self, guild_id: int, user_id: int, amount: int, algorithm: str = "mee6") -> tuple[int, int]:
        """Returns new (xp, level)."""
        pass

    @abc.abstractmethod
    async def get_leaderboard(self, guild_id: int, limit: int = 10, offset: int = 0):
        pass

    @abc.abstractmethod
    async def get_global_leaderboard(self, limit: int = 10, offset: int = 0):
        pass
    
    @abc.abstractmethod
    async def update_user_cosmetics(self, guild_id: int, user_id: int, **kwargs):
        pass

class SQLiteDB(BaseDB):
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None

    async def connect(self):
        self.conn = await aiosqlite.connect(self.db_path)
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                guild_id INTEGER,
                user_id INTEGER,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 0,
                background_id TEXT DEFAULT 'default',
                title_color TEXT DEFAULT '#FFFFFF',
                bar_color TEXT DEFAULT '#00FF00',
                bio TEXT DEFAULT '',
                prestige INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )
        try:
            await self.conn.execute("ALTER TABLE users ADD COLUMN bio TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            await self.conn.execute("ALTER TABLE users ADD COLUMN prestige INTEGER DEFAULT 0")
        except Exception:
            pass
            
        await self.conn.commit()
        log.info("SQLite database connected and tables initialized.")

    async def close(self):
        if self.conn:
            await self.conn.close()

    async def get_user(self, guild_id: int, user_id: int) -> dict:
        async with self.conn.execute(
            "SELECT xp, level, background_id, title_color, bar_color, bio, prestige FROM users WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "xp": row[0],
                    "level": row[1],
                    "background_id": row[2],
                    "title_color": row[3],
                    "bar_color": row[4],
                    "bio": row[5],
                    "prestige": row[6]
                }
            return {
                "xp": 0,
                "level": 0,
                "background_id": "default",
                "title_color": "#FFFFFF",
                "bar_color": "#00FF00",
                "bio": "",
                "prestige": 0
            }

    async def _calculate_level(self, xp: int, algorithm: str = "mee6") -> int:
        level = 0
        while True:
            if algorithm == "linear":
                required_xp = 100 * (level + 1)
            elif algorithm == "stevy":
                required_xp = int(100 * (1.5 ** level))
            else: # mee6 default
                required_xp = (5/6) * (level + 1) * (2 * ((level + 1)**2) + 27 * (level + 1) + 91)
                
            if xp >= required_xp:
                level += 1
            else:
                break
        return level

    async def add_user_xp(self, guild_id: int, user_id: int, amount: int, algorithm: str = "mee6", max_level: int = 0) -> tuple[int, int]:
        async with self.conn.execute("SELECT xp, level FROM users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)) as cursor:
            row = await cursor.fetchone()
            if row:
                current_xp, current_level = row
                new_xp = current_xp + amount
                new_level = await self._calculate_level(new_xp, algorithm)
                if max_level > 0 and new_level > max_level:
                    new_level = max_level
                
                await self.conn.execute(
                    "UPDATE users SET xp = ?, level = ? WHERE guild_id = ? AND user_id = ?",
                    (new_xp, new_level, guild_id, user_id)
                )
            else:
                new_xp = amount
                new_level = await self._calculate_level(new_xp, algorithm)
                if max_level > 0 and new_level > max_level:
                    new_level = max_level
                
                await self.conn.execute(
                    "INSERT INTO users (guild_id, user_id, xp, level) VALUES (?, ?, ?, ?)",
                    (guild_id, user_id, new_xp, new_level)
                )
        await self.conn.commit()
        return new_xp, new_level

    async def get_leaderboard(self, guild_id: int, limit: int = 10, offset: int = 0):
        async with self.conn.execute(
            "SELECT user_id, xp, level, prestige FROM users WHERE guild_id = ? ORDER BY prestige DESC, xp DESC LIMIT ? OFFSET ?",
            (guild_id, limit, offset)
        ) as cursor:
            return await cursor.fetchall()

    async def get_global_leaderboard(self, limit: int = 10, offset: int = 0):
        async with self.conn.execute(
            "SELECT user_id, SUM(xp) as total_xp, MAX(level) FROM users GROUP BY user_id ORDER BY total_xp DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ) as cursor:
            return await cursor.fetchall()

    async def update_user_cosmetics(self, guild_id: int, user_id: int, **kwargs):
        if not kwargs:
            return
        
        # Ensure user exists first
        await self.conn.execute(
            "INSERT OR IGNORE INTO users (guild_id, user_id) VALUES (?, ?)",
            (guild_id, user_id)
        )
        
        set_clauses = []
        values = []
        for k, v in kwargs.items():
            if k in ("background_id", "title_color", "bar_color", "bio", "prestige"):
                set_clauses.append(f"{k} = ?")
                values.append(v)
                
        if set_clauses:
            query = f"UPDATE users SET {', '.join(set_clauses)} WHERE guild_id = ? AND user_id = ?"
            values.extend([guild_id, user_id])
            await self.conn.execute(query, tuple(values))
            await self.conn.commit()


class MySQLDB(BaseDB):
    def __init__(self, **db_config):
        self.db_config = db_config
        self.pool = None

    async def connect(self):
        import aiomysql
        self.pool = await aiomysql.create_pool(**self.db_config, autocommit=True)
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        guild_id BIGINT,
                        user_id BIGINT,
                        xp BIGINT DEFAULT 0,
                        level INT DEFAULT 0,
                        background_id VARCHAR(255) DEFAULT 'default',
                        title_color VARCHAR(20) DEFAULT '#FFFFFF',
                        bar_color VARCHAR(20) DEFAULT '#00FF00',
                        bio VARCHAR(100) DEFAULT '',
                        prestige INT DEFAULT 0,
                        PRIMARY KEY (guild_id, user_id)
                    )
                    """
                )
                try:
                    await cur.execute("ALTER TABLE users ADD COLUMN bio VARCHAR(100) DEFAULT ''")
                except Exception:
                    pass
                try:
                    await cur.execute("ALTER TABLE users ADD COLUMN prestige INT DEFAULT 0")
                except Exception:
                    pass
        log.info("MySQL database connected and tables initialized.")

    async def close(self):
        if self.pool:
            self.pool.close()
            await self.pool.wait_closed()

    async def get_user(self, guild_id: int, user_id: int) -> dict:
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT xp, level, background_id, title_color, bar_color, bio, prestige FROM users WHERE guild_id = %s AND user_id = %s",
                    (guild_id, user_id)
                )
                row = await cur.fetchone()
                if row:
                    return {
                        "xp": row[0],
                        "level": row[1],
                        "background_id": row[2],
                        "title_color": row[3],
                        "bar_color": row[4],
                        "bio": row[5],
                        "prestige": row[6]
                    }
                return {
                    "xp": 0,
                    "level": 0,
                    "background_id": "default",
                    "title_color": "#FFFFFF",
                    "bar_color": "#00FF00",
                    "bio": "",
                    "prestige": 0
                }

    async def _calculate_level(self, xp: int, algorithm: str = "mee6") -> int:
        level = 0
        while True:
            if algorithm == "linear":
                required_xp = 100 * (level + 1)
            elif algorithm == "stevy":
                required_xp = int(100 * (1.5 ** level))
            else: # mee6 default
                required_xp = (5/6) * (level + 1) * (2 * ((level + 1)**2) + 27 * (level + 1) + 91)
                
            if xp >= required_xp:
                level += 1
            else:
                break
        return level

    async def add_user_xp(self, guild_id: int, user_id: int, amount: int, algorithm: str = "mee6", max_level: int = 0) -> tuple[int, int]:
        current = await self.get_user(guild_id, user_id)
        new_xp = current["xp"] + amount
        new_level = await self._calculate_level(new_xp, algorithm)
        if max_level > 0 and new_level > max_level:
            new_level = max_level
        
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO users (guild_id, user_id, xp, level)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE xp = VALUES(xp), level = VALUES(level)
                    """,
                    (guild_id, user_id, new_xp, new_level)
                )
        return new_xp, new_level

    async def get_leaderboard(self, guild_id: int, limit: int = 10, offset: int = 0):
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT user_id, xp, level, prestige FROM users WHERE guild_id = %s ORDER BY prestige DESC, xp DESC LIMIT %s OFFSET %s",
                    (guild_id, limit, offset)
                )
                return await cur.fetchall()

    async def get_global_leaderboard(self, limit: int = 10, offset: int = 0):
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT user_id, SUM(xp) as total_xp, MAX(level) FROM users GROUP BY user_id ORDER BY total_xp DESC LIMIT %s OFFSET %s",
                    (limit, offset)
                )
                return await cur.fetchall()

    async def update_user_cosmetics(self, guild_id: int, user_id: int, **kwargs):
        if not kwargs:
            return
            
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                # Ensure user exists
                await cur.execute(
                    "INSERT IGNORE INTO users (guild_id, user_id) VALUES (%s, %s)",
                    (guild_id, user_id)
                )
                
                set_clauses = []
                values = []
                for k, v in kwargs.items():
                    if k in ("background_id", "title_color", "bar_color", "bio", "prestige"):
                        set_clauses.append(f"{k} = %s")
                        values.append(v)
                        
                if set_clauses:
                    query = f"UPDATE users SET {', '.join(set_clauses)} WHERE guild_id = %s AND user_id = %s"
                    values.extend([guild_id, user_id])
                    await cur.execute(query, tuple(values))

