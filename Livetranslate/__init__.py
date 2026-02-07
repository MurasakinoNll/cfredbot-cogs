from .livetranslate import LiveTranslate


async def setup(bot):
    await bot.add_cog(LiveTranslate(bot))
