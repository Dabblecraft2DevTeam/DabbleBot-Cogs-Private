from .NameUpdate import NameUpdate


async def setup(bot):
    await bot.add_cog(NameUpdate(bot))