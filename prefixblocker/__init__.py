from .prefixblocker import PrefixBlocker

async def setup(bot):
    await bot.add_cog(PrefixBlocker(bot))
