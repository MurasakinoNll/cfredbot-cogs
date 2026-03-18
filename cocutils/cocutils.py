import discord
import wcwidth
from redbot.core import commands
from redbot.core.bot import Red

CHANNEL_ID = 1483917391980396605

ROLE_IDS = [
    1235277600151179364,
    1483520189902356641,
    1483519620018077918,
]


class CocUtils(commands.Cog):
    """Displays and auto-updates a list of members with specific roles."""

    def __init__(self, bot: Red):
        self.bot = bot
        # Two messages: [msg_id_1, msg_id_2]
        self._message_ids: list[int | None] = [None, None]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_role_members(
        self, guild: discord.Guild, role_id: int
    ) -> list[discord.Member]:
        role = guild.get_role(role_id)
        if role is None:
            return []
        return role.members

    def _display_width(self, text: str) -> int:
        w = wcwidth.wcswidth(text)
        return w if w > 0 else len(text)  # fallback if wcswidth returns -1

    def _safe_name(self, name: str) -> str:
        return f"\u202a{name}\u202c"

    def _build_block(self, guild: discord.Guild, role_id: int, color: str) -> str:
        members = self._get_role_members(guild, role_id)
        if not members:
            return f"**Role <@&{role_id}>:**\n```ansi\nNo members\n```"
        safe_names = [self._safe_name(m.display_name) for m in members]
        max_len = max(self._display_width(n) for n in safe_names)
        lines = []
        for m, name in zip(members, safe_names):
            pad = max_len - self._display_width(name)
            lines.append(
                f"\033[{color}m{name}{' ' * pad}\033[0m | \033[40m{m.id}\033[0m"
            )
        member_list = "\n".join(lines)

        return f"**Role <@&{role_id}>:**\n```ansi\n{member_list}\n```"

    def _build_contents(self, guild: discord.Guild) -> tuple[str, str]:
        """
        returns two message contents:
          msg1 -> Role 1 + divider + Role 2
          msg2 -> Role 3
        """
        block1 = self._build_block(guild, ROLE_IDS[0], "1;33")
        block2 = self._build_block(guild, ROLE_IDS[1], "1;32")
        block3 = self._build_block(guild, ROLE_IDS[2], "1;31")

        content1 = f"{block1}\n{block2}\n{block3}"
        content2 = ""
        return content1, content2

    async def _fetch_or_none(self, channel: discord.TextChannel, msg_id: int | None):
        if msg_id is None:
            return None
        try:
            return await channel.fetch_message(msg_id)
        except discord.NotFound:
            return None

    async def _refresh(self):
        channel = self.bot.get_channel(CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            return

        guild = channel.guild
        content1, content2 = self._build_contents(guild)

        for i, content in enumerate([content1, content2]):
            msg = await self._fetch_or_none(channel, self._message_ids[i])
            if msg is None:
                msg = await channel.send(content)
                self._message_ids[i] = msg.id
            elif msg.content != content:
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

        if any(rid in (before_ids ^ after_ids) for rid in ROLE_IDS):
            await self._refresh()

    # ------------------------------------------------------------------
    # Manual refresh command
    # ------------------------------------------------------------------

    @commands.command
    @commands.is_owner()
    async def refresh_roles(self, ctx: commands.Context):
        """Manually trigger a role-list refresh."""
        await self._refresh()
        await ctx.tick()
