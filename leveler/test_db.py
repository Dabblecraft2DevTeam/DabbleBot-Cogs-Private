import asyncio
import os
from database import SQLiteDB
import random

async def test():
    if os.path.exists("test.db"):
        os.remove("test.db")
    db = SQLiteDB("test.db")
    await db.connect()
    
    # 1. Give 500,000 XP (max level 500)
    xp1, lvl1 = await db.add_user_xp(1, 1, 500000, algorithm="mee6", max_level=500)
    print(f"After addxp 500000: xp={xp1} level={lvl1}")
    
    # 2. Type message
    xp2, lvl2 = await db.add_user_xp(1, 1, 20, algorithm="mee6", max_level=500)
    print(f"After message 20: xp={xp2} level={lvl2}")
    
    # 3. Type another message
    xp3, lvl3 = await db.add_user_xp(1, 1, 20, algorithm="mee6", max_level=500)
    print(f"After message 20: xp={xp3} level={lvl3}")
    
    user = await db.get_user(1, 1)
    print(f"Final User DB state: xp={user['xp']} level={user['level']}")
    
    await db.close()

if __name__ == "__main__":
    asyncio.run(test())
