from .quotegame import QuoteGame

async def setup(bot):
    cog = QuoteGame(bot)
    await bot.add_cog(cog)
