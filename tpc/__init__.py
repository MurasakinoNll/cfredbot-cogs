from .tpc import TPC


async def setup(bot):
    await bot.add_cog(TPC(bot))
