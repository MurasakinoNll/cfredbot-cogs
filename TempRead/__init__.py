from .measuretemp import MeasureTemp


async def setup(bot):
    await bot.add_cog(MeasureTemp(bot))
