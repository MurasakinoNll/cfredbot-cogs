from .rpc import RPC


async def setup(bot):
    await bot.add_cog(RPC(bot))
