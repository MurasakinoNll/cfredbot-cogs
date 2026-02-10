from .snowyroullete import SnowyRoullete


async def setup(bot):
    await bot.add_cog(SnowyRoullete(bot))
