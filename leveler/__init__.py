from .leveler import Leveler

async def setup(bot):
    cog = Leveler(bot)
    await bot.add_cog(cog)
