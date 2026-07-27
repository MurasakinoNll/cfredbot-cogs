from .tpc import TPC


async def setup(bot):
    await bot.add_cog(TPC(bot))


async def _migrate_allowlist(self):
    current = await self.config.allowlist()
    if isinstance(current, list):
        migrated = {str(uid): "moderator" for uid in current}
        await self.config.allowlist.set(migrated)
