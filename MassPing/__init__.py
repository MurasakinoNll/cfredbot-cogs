from .sping import SPing


async def setup(bot):
    await bot.add_cog(SPing(bot))
