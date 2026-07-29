import discord
from redbot.core import commands, Config
import datetime
import asyncio

PERMISSION_TIERS = {
    "channel_manager": discord.Permissions(manage_channels=True),
    "moderator": discord.Permissions(
        manage_messages=True, kick_members=True, moderate_members=True
    ),
    "admin": discord.Permissions(administrator=True),
}

SELF_SERVICE_TIERS = ["channel_manager", "moderator"]

ROLE_NAME_PREFIX = "TPC-"
CONFIRM_PHRASE = "Y0U$H4LLN0TP4$$"


class TPC(commands.Cog):
    """Temporary, scoped Discord permission grants for allowlisted testers."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=9876543210, force_registration=True
        )
        self.config.register_global(
            allowlist={},  # {user_id_str: tier_name}
            active_grants={},  # {user_id_str: {"expiry": ts, "guild_id": id, "tier": name, "role_id": id}}
            log_channel=None,
        )
        self.bot.loop.create_task(self._migrate_data())

    async def _migrate_data(self):
        current_allowlist = await self.config.allowlist()
        if isinstance(current_allowlist, list):
            migrated = {str(uid): "moderator" for uid in current_allowlist}
            await self.config.allowlist.set(migrated)

        current_grants = await self.config.active_grants()
        cleaned_grants = {
            uid: data for uid, data in current_grants.items() if isinstance(data, dict)
        }
        if len(cleaned_grants) != len(current_grants):
            await self.config.active_grants.set(cleaned_grants)

    # ---------------------------------------------------------------

    @commands.command(name="tpcadd")
    @commands.is_owner()
    async def tpc_add(self, ctx, user: discord.User, tier: str = "moderator"):
        """Add a user to the allowlist with an assigned permission tier."""
        if tier not in PERMISSION_TIERS:
            await ctx.send(
                f"Unknown tier `{tier}`. Options: {', '.join(PERMISSION_TIERS)}"
            )
            return
        async with self.config.allowlist() as allowlist:
            allowlist[str(user.id)] = tier
        await ctx.send(f"Added {user} to the allowlist with tier `{tier}`.")

    @commands.command(name="tpcremove")
    @commands.is_owner()
    async def tpc_remove(self, ctx, user: discord.User):
        """Remove a user from the allowlist."""
        async with self.config.allowlist() as allowlist:
            if str(user.id) not in allowlist:
                await ctx.send(f"{user} is not on the allowlist.")
                return
            del allowlist[str(user.id)]
        await ctx.send(f"Removed {user} from the allowlist.")

    @commands.command(name="tpclist")
    @commands.is_owner()
    async def tpc_list(self, ctx):
        """Show current allowlist and active grants."""
        allowlist = await self.config.allowlist()
        grants = await self.config.active_grants()

        lines = ["**Allowlist:**"]
        if not allowlist:
            lines.append("(empty)")
        for uid, tier in allowlist.items():
            user = self.bot.get_user(int(uid))
            lines.append(f"- {user or uid}: `{tier}`")

        lines.append("\n**Active grants:**")
        if not grants:
            lines.append("(none)")
        for uid, data in grants.items():
            user = self.bot.get_user(int(uid))
            expiry = datetime.datetime.utcfromtimestamp(data["expiry"]).strftime(
                "%H:%M:%S UTC"
            )
            lines.append(
                f"- {user or uid}: `{data['tier']}` in guild {data['guild_id']} until {expiry}"
            )

        await ctx.send("\n".join(lines))

    @commands.command(name="tpcsetlog")
    @commands.is_owner()
    async def tpc_setlog(self, ctx, channel: discord.TextChannel):
        """Set the channel used for TPC audit logs."""
        await self.config.log_channel.set(channel.id)
        await ctx.send(f"TPC audit log channel set to {channel.mention}.")

    @commands.command(name="tpcgrant")
    @commands.is_owner()
    async def tpc_grant_manual(
        self,
        ctx,
        user: discord.User,
        tier: str = "moderator",
        server_id: int = None,
        minutes: int = 5,
    ):
        """
        Manually grant a tier to a user (owner only, required for the admin tier).

        Works in DMs — specify <server_id> to target a specific server.
        If run inside a server and server_id is omitted, defaults to the current server.
        """
        if tier not in PERMISSION_TIERS:
            await ctx.send(
                f"Unknown tier `{tier}`. Options: {', '.join(PERMISSION_TIERS)}"
            )
            return

        guild, member, error = await self._resolve_guild_and_member(
            ctx, user.id, server_id
        )
        if error:
            await ctx.send(error)
            return

        await self._do_grant(ctx, guild, member, tier, minutes)

    # ---------------------------------------------------------------

    @commands.command(name="tpc")
    async def tpc(
        self, ctx, confirm: str = None, server_id: int = None, minutes: int = 5
    ):
        """
        Self-grant your assigned permission tier in a server for N minutes (default 5).

        Works in DMs — specify <server_id> to target a specific server.
        If run inside a server and server_id is omitted, defaults to the current server.
        If your assigned tier isn't self-servable (e.g. admin), you'll be granted the
        highest tier that is self-servable instead.
        """
        allowlist = await self.config.allowlist()
        assigned_tier = allowlist.get(str(ctx.author.id))

        if assigned_tier is None:
            await ctx.send("Unable to process that command.")
            return

        if confirm != CONFIRM_PHRASE:
            await ctx.send("Missing or incorrect confirmation string.")
            return

        if minutes <= 0 or minutes > 30:
            await ctx.send("Minutes must be between 1 and 30.")
            return

        guild, member, error = await self._resolve_guild_and_member(
            ctx, ctx.author.id, server_id
        )
        if error:
            await ctx.send(error)
            return

        grant_tier = (
            assigned_tier
            if assigned_tier in SELF_SERVICE_TIERS
            else SELF_SERVICE_TIERS[-1]
        )

        await self._do_grant(
            ctx, guild, member, grant_tier, minutes, assigned_tier=assigned_tier
        )

    @commands.command(name="tpcrevoke")
    @commands.is_owner()
    async def tpc_revoke(self, ctx, user: discord.User):
        """Manually end an active grant early (owner only)."""
        grants = await self.config.active_grants()
        if str(user.id) not in grants:
            await ctx.send(f"{user} has no active grant.")
            return
        await self._revoke_by_id(user.id, reason=f"manual by {ctx.author}")
        await ctx.send(f"Revoked active grant for {user}.")

    # ---------------------------------------------------------------

    async def _resolve_guild_and_member(self, ctx, user_id: int, server_id: int = None):
        """
        Resolve a target guild + that user's Member object in it.
        Returns (guild, member, error_message). error_message is None on success.
        """
        if server_id is not None:
            guild = self.bot.get_guild(server_id)
            if guild is None:
                return None, None, "Bot is not in a server with that ID."
        elif ctx.guild is not None:
            guild = ctx.guild
        else:
            return None, None, "Specify a server_id (this command was run in DMs)."

        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound:
                return None, None, f"That user is not a member of {guild.name}."
            except discord.HTTPException:
                return None, None, "Failed to look up membership in that server."

        return guild, member, None

    async def _get_or_create_role(
        self, guild: discord.Guild, tier: str
    ) -> discord.Role:
        role_name = f"{ROLE_NAME_PREFIX}{tier}"
        existing = discord.utils.get(guild.roles, name=role_name)
        if existing:
            return existing
        return await guild.create_role(
            name=role_name,
            permissions=PERMISSION_TIERS[tier],
            reason="TPC permission tier role (auto-created)",
        )

    async def _do_grant(
        self,
        ctx,
        guild: discord.Guild,
        member: discord.Member,
        tier: str,
        minutes: int,
        assigned_tier: str = None,
    ):
        role = await self._get_or_create_role(guild, tier)
        try:
            await member.add_roles(role, reason=f"TPC grant ({tier}), {minutes}m")
        except discord.Forbidden:
            await ctx.send(
                f"Bot lacks permission to assign the `{role.name}` role in {guild.name} (check role hierarchy)."
            )
            return

        expiry = datetime.datetime.utcnow() + datetime.timedelta(minutes=minutes)
        async with self.config.active_grants() as grants:
            grants[str(member.id)] = {
                "expiry": expiry.timestamp(),
                "guild_id": guild.id,
                "tier": tier,
                "role_id": role.id,
            }

        downgrade_note = ""
        if assigned_tier and assigned_tier != tier:
            downgrade_note = f" (your assigned tier `{assigned_tier}` isn't self-servable, granted `{tier}` instead)"

        await ctx.send(
            f"Granted `{tier}` to {member.mention} in {guild.name} until {expiry.strftime('%H:%M:%S UTC')}.{downgrade_note}"
        )
        await self._log(
            f"GRANT: {member} ({member.id}) tier={tier} assigned={assigned_tier or tier} guild={guild.name} ({guild.id}) until {expiry.isoformat()}"
        )

        asyncio.create_task(self._auto_revoke(member.id, minutes * 60))

    async def _auto_revoke(self, user_id: int, delay: int):
        await asyncio.sleep(delay)
        await self._revoke_by_id(user_id, reason="expired")

    async def _revoke_by_id(self, user_id: int, reason: str = "manual"):
        grants = await self.config.active_grants()
        data = grants.get(str(user_id))
        if not data:
            return

        guild = self.bot.get_guild(data["guild_id"])
        if guild:
            member = guild.get_member(user_id)
            role = guild.get_role(data["role_id"])
            if member and role and role in member.roles:
                try:
                    await member.remove_roles(role, reason=f"TPC revoke ({reason})")
                except discord.Forbidden:
                    pass

        async with self.config.active_grants() as g:
            g.pop(str(user_id), None)

        await self._log(
            f"REVOKE ({reason}): user_id={user_id} guild={data['guild_id']} tier={data['tier']}"
        )

    # ---------------------------------------------------------------

    async def has_temp_owner(self, user_id: int) -> bool:
        grants = await self.config.active_grants()
        return str(user_id) in grants

    async def _log(self, message: str):
        log_channel_id = await self.config.log_channel()
        if log_channel_id:
            channel = self.bot.get_channel(log_channel_id)
            if channel:
                await channel.send(f"`[TPC LOG]` {message}")
