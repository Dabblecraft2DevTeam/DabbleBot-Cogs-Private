from .customping import CustomPing

__red_end_user_data_statement__ = "This cog does not store any End User Data."


async def setup(bot):
    cog = CustomPing(bot)
    cog.old_ping = bot.get_command("ping")
    if cog.old_ping:
        bot.remove_command(cog.old_ping.name)
    await bot.add_cog(cog)
