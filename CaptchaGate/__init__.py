from .captchagate import CaptchaGate

async def setup(bot):
    """Sets up the CaptchaGate cog."""
    await bot.add_cog(CaptchaGate(bot))