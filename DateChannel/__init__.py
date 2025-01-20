from .DateChannelCog import DateChannelCog


async def setup(bot):
    await bot.add_cog(DateChannelCog(bot))