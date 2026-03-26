from .ptysessionmgr import ExecPty


async def setup(bot):
    await bot.add_cog(ExecPty(bot))
