from .execve import ExecVE


async def setup(bot):
    await bot.add_cog(ExecVE(bot))
