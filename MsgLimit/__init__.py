from .msglimit import MsgLimit


async def setup(bot):
    await bot.add_cog(MsgLimit(bot))
