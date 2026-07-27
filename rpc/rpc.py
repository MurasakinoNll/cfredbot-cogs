import discord
from redbot.core import commands, Config


class RPC(commands.Cog):
    """Remote ban/unban across any server the bot is in, without needing to join it."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=1122334455, force_registration=True
        )
        self.config.register_global(log_channel=None)

    async def _authorized(self, ctx) -> bool:
        if await self.bot.is_owner(ctx.author):
            return True
        tpc_cog = self.bot.get_cog("TPC")
        if tpc_cog and await tpc_cog.has_temp_owner(ctx.author.id):
            return True
        return False

    @commands.command(name="rpcsetlog")
    @commands.is_owner()
    async def rpc_setlog(self, ctx, channel: discord.TextChannel):
        """Set the channel used for RPC audit logs."""
        await self.config.log_channel.set(channel.id)
        await ctx.send(f"RPC audit log channel set to {channel.mention}.")

    @commands.command(name="rpc")
    async def rpc(self, ctx, action: str, uid: int, serverid: int):
        """
        Remotely ban or unban a user in any guild the bot is in.

        Usage: !rpc ban <uid> <serverid>
               !rpc unban <uid> <serverid>
        """
        if not await self._authorized(ctx):
            await ctx.send("Unable to process that command.")
            return

        action = action.lower()
        if action not in ("ban", "unban"):
            await ctx.send("Action must be `ban` or `unban`.")
            return

        guild = self.bot.get_guild(serverid)
        if guild is None:
            await ctx.send("Bot is not in a guild with that ID.")
            return

        user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
        if user is None:
            await ctx.send("Could not resolve that user ID.")
            return

        me = guild.me
        if action == "ban":
            if not me.guild_permissions.ban_members:
                await ctx.send(f"Bot lacks ban permissions in {guild.name}.")
                return
            try:
                await guild.ban(
                    user, reason=f"RPC ban issued by {ctx.author} ({ctx.author.id})"
                )
            except discord.Forbidden:
                await ctx.send(f"Forbidden: could not ban {user} in {guild.name}.")
                return
            except discord.HTTPException as e:
                await ctx.send(f"Failed to ban: {e}")
                return
        else:
            if not me.guild_permissions.ban_members:
                await ctx.send(f"Bot lacks unban permissions in {guild.name}.")
                return
            try:
                await guild.unban(
                    user, reason=f"RPC unban issued by {ctx.author} ({ctx.author.id})"
                )
            except discord.NotFound:
                await ctx.send(f"{user} is not currently banned in {guild.name}.")
                return
            except discord.Forbidden:
                await ctx.send(f"Forbidden: could not unban {user} in {guild.name}.")
                return
            except discord.HTTPException as e:
                await ctx.send(f"Failed to unban: {e}")
                return

        await ctx.send(
            f"{action.capitalize()}ned {user} ({uid}) in {guild.name} ({serverid})."
        )
        await self._log(
            f"RPC {action.upper()}: {ctx.author} ({ctx.author.id}) {action}ned user {user} ({uid}) in guild {guild.name} ({serverid})"
        )

    async def _log(self, message):
        log_channel_id = await self.config.log_channel()
        if log_channel_id:
            channel = self.bot.get_channel(log_channel_id)
            if channel:
                await channel.send(f"`[RPC LOG]` {message}")
