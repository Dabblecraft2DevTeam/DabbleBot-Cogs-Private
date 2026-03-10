from .nbzhc_rank import NBZHCRank

async def setup(bot):
    await bot.add_cog(NBZHCRank(bot))