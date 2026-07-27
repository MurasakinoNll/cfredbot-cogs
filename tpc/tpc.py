import discord
from redbot.core import commands, Config
import datetime

CONFIRM_PHRASE = "d063254ed4123704a160bc4a357897be79c9d7873314e23a89c7a7baa64e385"
# ^ Public string, not a secret. Access is controlled entirely by the allowlist below.


class TPC(commands.Cog):
    """Temporary owner grants for allowlisted testers."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=9876543210, force_registration=True
        )
        default_global = {
            "allowlist": [],  # list of user IDs allowed to self-grant
            "active_grants": {},  # {user_id: expiry_timestamp}
            "log_channel": None,  # channel id for audit logs
        }
        self.config.register_global(**default_global)

    # ---- setup/admin commands (real bot owner only) ----

    @commands.command(name="tpcadd")
    @commands.is_owner()
    async def tpc_add(self, ctx, user: discord.User):
        """Add a user to the TPC allowlist."""
        async with self.config.allowlist() as allowlist:
            if user.id in allowlist:
                await ctx.send(f"{user} is already allowlisted.")
                return
            allowlist.append(user.id)
        await ctx.send(f"Added {user} to the TPC allowlist.")

    @commands.command(name="tpcremove")
    @commands.is_owner()
    async def tpc_remove(self, ctx, user: discord.User):
        """Remove a user from the TPC allowlist."""
        async with self.config.allowlist() as allowlist:
            if user.id not in allowlist:
                await ctx.send(f"{user} is not on the allowlist.")
                return
            allowlist.remove(user.id)
        await ctx.send(f"Removed {user} from the TPC allowlist.")

    @commands.command(name="tpcsetlog")
    @commands.is_owner()
    async def tpc_setlog(self, ctx, channel: discord.TextChannel):
        """Set the channel used for TPC audit logs."""
        await self.config.log_channel.set(channel.id)
        await ctx.send(f"TPC audit log channel set to {channel.mention}.")

    # ---- self-service grant ----

    @commands.command(name="tpc")
    async def tpc(self, ctx, confirm: str = None):
        """Self-grant 5 minutes of temporary owner access (allowlisted users only)."""
        allowlist = await self.config.allowlist()
        if ctx.author.id not in allowlist:
            # Deliberately vague — don't reveal whether allowlist check or confirm failed
            await ctx.send("Unable to process that command.")
            return

        if confirm != CONFIRM_PHRASE:
            await ctx.send("Missing or incorrect confirmation string.")
            return

        expiry = datetime.datetime.utcnow() + datetime.timedelta(minutes=5)
        async with self.config.active_grants() as grants:
            grants[str(ctx.author.id)] = expiry.timestamp()

        await ctx.send(
            f"Temporary owner granted to {ctx.author.mention} until {expiry.strftime('%H:%M:%S UTC')}."
        )
        await self._log(
            ctx.guild,
            f"TPC GRANT: {ctx.author} ({ctx.author.id}) self-granted temp owner until {expiry.isoformat()}",
        )

    @commands.command(name="tpcrevoke")
    @commands.is_owner()
    async def tpc_revoke(self, ctx, user: discord.User):
        """Revoke an active TPC grant early (real owner only)."""
        async with self.config.active_grants() as grants:
            if str(user.id) not in grants:
                await ctx.send(f"{user} has no active TPC grant.")
                return
            del grants[str(user.id)]
        await ctx.send(f"Revoked TPC grant for {user}.")
        await self._log(
            ctx.guild,
            f"TPC REVOKE: {ctx.author} manually revoked grant for {user} ({user.id})",
        )

    # ---- helper: check if a user currently has an active grant ----

    async def has_temp_owner(self, user_id: int) -> bool:
        grants = await self.config.active_grants()
        expiry = grants.get(str(user_id))
        if expiry is None:
            return False
        if datetime.datetime.utcnow().timestamp() > expiry:
            async with self.config.active_grants() as g:
                g.pop(str(user_id), None)
            return False
        return True

    async def _log(self, guild, message):
        log_channel_id = await self.config.log_channel()
        if log_channel_id:
            channel = self.bot.get_channel(log_channel_id)
            if channel:
                await channel.send(f"`[TPC LOG]` {message}")
