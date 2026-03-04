from .musicstatus import MusicStatus


async def setup(bot):
    await bot.add_cog(MusicStatus(bot))