import discord
from redbot.core import commands
from redbot.core.bot import Red

CHANNEL_ID = 1483917391980396605
ROLE_ID = 1483920076766838968


class CocUtils(commands.Cog):
    """Displays and auto-updates a list of members with a specific role."""

    def __init__(self, bot: Red):
        self.bot = bot
        self._message_id: int | None = None  # cached message we own

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_members_with_role(self) -> list[discord.Member]:
        """Return all cached members that have the target role."""
        role = None
        for guild in self.bot.guilds:
            role = guild.get_role(ROLE_ID)
            if role:
                break
        if role is None:
            return []
        return role.members

    def _build_content(self, members: list[discord.Member]) -> str:
        if not members:
            member_list = "_No members_"
        else:
            member_list = "\n".join(f"• {m.display_name} ({m.id})" for m in members)
        return f"**Role <@&{ROLE_ID}>**\n{member_list}"

    async def _get_or_create_message(
        self, channel: discord.TextChannel, content: str
    ) -> discord.Message:
        """
        Try to find our previously posted message by scanning recent history.
        If not found, post a new one and cache its ID.
        """
        if self._message_id:
            try:
                msg = await channel.fetch_message(self._message_id)
                return msg
            except discord.NotFound:
                self._message_id = None

        # Scan last 50 messages for one we sent
        async for msg in channel.history(limit=50):
            if msg.author == self.bot.user and msg.content.startswith(
                f"**Role <@&{ROLE_ID}>**"
            ):
                self._message_id = msg.id
                return msg

        # Nothing found — post fresh
        msg = await channel.send(content)
        self._message_id = msg.id
        return msg

    async def _refresh(self):
        """Rebuild and edit (or post) the role-member message."""
        channel = self.bot.get_channel(CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            return

        members = await self._get_members_with_role()
        content = self._build_content(members)
        msg = await self._get_or_create_message(channel, content)

        if msg.content != content:
            await msg.edit(content=content)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self):
        await self._refresh()

    # ------------------------------------------------------------------
    # Role-change listener
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        before_ids = {r.id for r in before.roles}
        after_ids = {r.id for r in after.roles}

        # Only care if our target role was added or removed
        if ROLE_ID in (before_ids ^ after_ids):
            await self._refresh()

    # ------------------------------------------------------------------
    # Manual refresh command (owner / admin use)
    # ------------------------------------------------------------------

    @commands.is_owner()
    async def refresh_roles(self, ctx: commands.Context):
        """Manually trigger a role-list refresh."""
        await self._refresh()
        await ctx.tick()
