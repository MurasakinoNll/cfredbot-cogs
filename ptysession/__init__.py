from .ptysessionmgr import ExecPTY


async def setup(bot):
    await bot.add_cog(ExecPTY(bot))
