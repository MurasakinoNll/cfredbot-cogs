import unicodedata
import os
import json
import discord
import aiohttp
from urllib.parse import quote
from redbot.core import commands
from redbot.core.bot import Red

CHANNEL_ID = 1483917391980396605
ANNOUNCE_ID = 1484351000788598927
ROLE_IDS = [
    1235277600151179364,
    1483520189902356641,
    1483519620018077918,
]

CLAN_ID = "#2YUYR2LPV"
CLASH_APIKEY = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiIsImtpZCI6IjI4YTMxOGY3LTAwMDAtYTFlYi03ZmExLTJjNzQzM2M2Y2NhNSJ9.eyJpc3MiOiJzdXBlcmNlbGwiLCJhdWQiOiJzdXBlcmNlbGw6Z2FtZWFwaSIsImp0aSI6Ijc4ODcxNTk3LTgxZDQtNDEzMS1hNTVmLWZkMjljMTE0NDM0ZiIsImlhdCI6MTc3Mzk2NzE5OSwic3ViIjoiZGV2ZWxvcGVyLzZjOGMxYmRjLWI3ODQtMjA5Ny0wOGQ5LTdiMWEzYTM2Y2Y2MCIsInNjb3BlcyI6WyJjbGFzaCJdLCJsaW1pdHMiOlt7InRpZXIiOiJkZXZlbG9wZXIvc2lsdmVyIiwidHlwZSI6InRocm90dGxpbmcifSx7ImNpZHJzIjpbIjE3Ni4yOC4xNDkuMjUyIl0sInR5cGUiOiJjbGllbnQifV19.JQmdedEYUKHwYyiHPlEXxSyplf8MyCx6dgk4QFJuRs3TrroFLYoXAICNPVsyaupmaioKSUra8sfOTwUPToyXdA"

class CocUtils(commands.Cog):
    """Displays and auto-updates a list of members with specific roles."""

    def __init__(self, bot: Red):
        self.bot = bot
        # Two messages: [msg_id_1, msg_id_2]
        self._message_ids: list[int | None] = [None, None]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_altnames(self) -> dict[str, str]:
        path = os.path.join(os.path.dirname(__file__), "data.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    async def _refresh(self):
        channel = self.bot.get_channel(CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            return

        guild = channel.guild
        altnames = self._load_altnames()         
        content1, content2 = self._build_contents(guild, altnames)

        for i, content in enumerate([content1, content2]):
            msg = await self._fetch_or_none(channel, self._message_ids[i])
            if msg is None:
                msg = await channel.send(content)
                self._message_ids[i] = msg.id
            elif msg.content != content:
                await msg.edit(content=content)

    def _get_role_members(
        self, guild: discord.Guild, role_id: int
    ) -> list[discord.Member]:
        role = guild.get_role(role_id)
        if role is None:
            return []
        return role.members

    def _safe_name(self, name: str) -> str:
        return f"\u202a{name}\u202c"

    def _display_width(self, text: str) -> int:
        clean = text.replace("\u202a", "").replace("\u202c", "")
        return len(clean)

    def _build_block(self, guild: discord.Guild, members: list[discord.Member], role_id: int | None, color: str, max_len: int, altnames: dict[str, str]) -> str:
        header = f"<@&{role_id}>" if role_id else "<@&1483520189902356641> and <@&1483519620018077918>"
        if not members:
            return f"**{header}:**\n```ansi\nNo members\n```"

        lines = []
        for m in members:
            name = self._safe_name(m.display_name)
            emoji_count = sum(1 for char in name if unicodedata.category(char) == 'So')
            pad = max_len - self._display_width(name) - emoji_count
            alt = altnames.get(str(m.id), "unknown IGN")
            lines.append(
                f"\033[{color}m{name}{' ' * pad}\033[0m | \033[1;37m{alt}\033[0m"
            )

        member_list = "\n".join(lines)
        return f"**{header}:**\n```ansi\n{member_list}\n```"

    def _build_contents(self, guild: discord.Guild, altnames: dict[str, str]) -> tuple[str, str]:
        members1 = self._get_role_members(guild, ROLE_IDS[0])
        members2 = self._get_role_members(guild, ROLE_IDS[1])
        members3 = self._get_role_members(guild, ROLE_IDS[2])

        ids2 = {m.id for m in members2}
        ids3 = {m.id for m in members3}
        shared_ids = ids2 & ids3
        members4 = [m for m in members2 if m.id in shared_ids]

        all_members = members1 + members2 + members3 + members4
        max_len = max(
            (self._display_width(self._safe_name(m.display_name)) for m in all_members),
            default=0
        )

        block1 = self._build_block(guild, members1, ROLE_IDS[0], "1;33", max_len, altnames)
        block2 = self._build_block(guild, members2, ROLE_IDS[1], "1;34", max_len, altnames)
        block3 = self._build_block(guild, members3, ROLE_IDS[2], "1;31", max_len, altnames)
        block4 = self._build_block(guild, members4, None,        "1;36", max_len, altnames)

        content1 = f"{block1}\n{block2}"
        content2 = f"\n{block3}\n{block4}"
        return content1, content2
    async def _fetch_or_none(self, channel: discord.TextChannel, msg_id: int | None):
        if msg_id is None:
            return None
        try:
            return await channel.fetch_message(msg_id)
        except discord.NotFound:
            return None

    # async def _refresh(self):
    #     channel = self.bot.get_channel(CHANNEL_ID)
    #     if not isinstance(channel, discord.TextChannel):
    #         return
    #
    #     guild = channel.guild
    #     content1, content2 = self._build_contents(guild)
    #
    #     for i, content in enumerate([content1, content2]):
    #         msg = await self._fetch_or_none(channel, self._message_ids[i])
    #         if msg is None:
    #             msg = await channel.send(content)
    #             self._message_ids[i] = msg.id
    #         elif msg.content != content:
    #             await msg.edit(content=content)

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

    @commands.is_owner()
    @commands.command()
    async def clanstat(self, ctx: commands.Context):
        encodedurlid = quote(CLAN_ID, safe="")
        url = f"https://api.clashofclans.com/v1/clans/{encodedurlid}/currentwar"
        header = {"Authorization": f"Bearer {CLASH_APIKEY}"}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=header) as response:
                if response.status != 200:
                    await ctx.send(f"coc api shat itself: {response.status}")
                    return
                data = await response.json()

        out = json.dumps(data, indent=2)
        announcechannel = self.bot.get_channel(ANNOUNCE_ID)
        if not isinstance(announcechannel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)):
            await ctx.send("announce channel not found.")
            return
        chunk_size = 1990
        chunks = [out[i:i+chunk_size] for i in range(0, len(out), chunk_size)]
        for chunk in chunks:
            await announcechannel.send(f"```json\n{chunk}\n```")
