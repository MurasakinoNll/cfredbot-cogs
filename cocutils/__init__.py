from .cocutils import CocUtils


async def setup(bot):
    await bot.add_cog(CocUtils(bot))
