import re
import unicodedata
import os
import json
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import quote

import aiohttp
from attr import s
import discord
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
CLASH_APIKEY = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiIsImtpZCI6IjI4YTMxOGY3LTAwMDAtYTFlYi03ZmExLTJjNzQzM2M2Y2NhNSJ9.eyJpc3MiOiJzdXBlcmNlbGwiLCJhdWQiOiJzdXBlcmNlbGw6Z2FtZWFwaSIsImp0aSI6Ijg2N2E4N2Q4LTRiZGYtNDI3NC1hOWUyLWNjNDFkODNiZGVmMiIsImlhdCI6MTc3Mzk2OTE1MSwic3ViIjoiZGV2ZWxvcGVyLzZjOGMxYmRjLWI3ODQtMjA5Ny0wOGQ5LTdiMWEzYTM2Y2Y2MCIsInNjb3BlcyI6WyJjbGFzaCJdLCJsaW1pdHMiOlt7InRpZXIiOiJkZXZlbG9wZXIvc2lsdmVyIiwidHlwZSI6InRocm90dGxpbmcifSx7ImNpZHJzIjpbIjE3Ni4yOC4xNDkuMjUyIiwiNS45NS4yNTAuNzEiXSwidHlwZSI6ImNsaWVudCJ9XX0.4A7gfJGrghRbetI6bFN1A8rJE9GOex2hV45oSv0xNJekKup4AEmnaa5rjfN0rjociaoXXH2qTDEc-wbbn4GPAQ"


###############################################################################
### DATACLASSES
###############################################################################

@dataclass
class WarMember:
    tag: str
    name: str
    townhall_level: int
    map_position: int
    opponent_attacks: int
    attacks: list[dict] = field(default_factory=list)
    best_opponent_attack: dict | None = None

@dataclass
class WarClan:
    tag: str
    name: str
    clan_level: int
    attacks: int
    stars: int
    destruction: float
    members: list[WarMember]

@dataclass
class WarState:
    state: str
    team_size: int
    attacks_per_member: int
    preparation_start: str
    start_time: str
    end_time: str
    clan: WarClan
    opponent: WarClan


###############################################################################
### COG
###############################################################################

class CocUtils(commands.Cog):
    """Displays and auto-updates a list of members with specific roles."""

    def __init__(self, bot: Red):
        self.bot = bot
        self._message_ids: list[int | None] = [None, None]
        self._war_message_ids: list[int | None] = []
        self._war_task: asyncio.Task | None = None

   
    ###########################################################################
    ### LIFECYCLE
    ###########################################################################

    @commands.Cog.listener()
    async def on_ready(self):
        await self._refresh()
        if self._war_task is None or self._war_task.done():
            self._war_task = asyncio.ensure_future(self._war_loop())

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        before_ids = {r.id for r in before.roles}
        after_ids = {r.id for r in after.roles}
        if any(rid in (before_ids ^ after_ids) for rid in ROLE_IDS):
            await self._refresh()

    ###########################################################################
    ### COMMANDS
    ###########################################################################

    @commands.is_owner()
    @commands.command()
    async def refresh_roles(self, ctx: commands.Context):
        """Manually trigger a role-list refresh."""
        await self._refresh()
        await ctx.tick()

    @commands.is_owner()
    @commands.command()
    async def clanstat(self, ctx: commands.Context):
        """Manually trigger a war status refresh."""
        await self._fetch_and_post_war()
        await ctx.tick()

    ###########################################################################
    ### ROLE LIST
    ###########################################################################

    def _load_altnames(self) -> dict[str, str]:
        path = os.path.join(os.path.dirname(__file__), "data.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _get_role_members(self, guild: discord.Guild, role_id: int) -> list[discord.Member]:
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

    ###########################################################################
    ### WAR — API
    ###########################################################################

    async def _fetch_war_data(self) -> dict | None:
        encoded = quote(CLAN_ID, safe="")
        url = f"https://api.clashofclans.com/v1/clans/{encoded}/currentwar"
        headers = {
            "Authorization": f"Bearer {CLASH_APIKEY}",
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    print(f"[cocutils] CoC API error: {resp.status}")
                    return None
                return await resp.json()

    ###########################################################################
    ### WAR — PARSING
    ###########################################################################

    def _parse_war(self, data: dict) -> WarState:
        def parse_members_full(members_data: list) -> list[WarMember]:
            result = []
            for m in members_data:
                result.append(WarMember(
                    tag=m.get("tag", ""),
                    name=m.get("name", ""),
                    townhall_level=m.get("townhallLevel", 0),
                    map_position=m.get("mapPosition", 0),
                    opponent_attacks=m.get("opponentAttacks", 0),
                    attacks=m.get("attacks", []),
                    best_opponent_attack=m.get("bestOpponentAttack"),
                ))
            return sorted(result, key=lambda m: m.map_position)

        def parse_clan_full(c: dict) -> WarClan:
            return WarClan(
                tag=c.get("tag", ""),
                name=c.get("name", ""),
                clan_level=c.get("clanLevel", 0),
                attacks=c.get("attacks", 0),
                stars=c.get("stars", 0),
                destruction=c.get("destructionPercentage", 0.0),
                members=parse_members_full(c.get("members", [])),
            )

        def parse_opponent(c: dict) -> WarClan:
            members = [
                WarMember(
                    tag=m.get("tag", ""),
                    name=m.get("name", ""),
                    townhall_level=m.get("townhallLevel", 0),
                    map_position=m.get("mapPosition", 0),
                    opponent_attacks=0,
                )
                for m in sorted(c.get("members", []), key=lambda m: m.get("mapPosition", 0))
            ]
            return WarClan(
                tag=c.get("tag", ""),
                name=c.get("name", ""),
                clan_level=c.get("clanLevel", 0),
                attacks=c.get("attacks", 0),
                stars=c.get("stars", 0),
                destruction=c.get("destructionPercentage", 0.0),
                members=members,
            )

        return WarState(
            state=data.get("state", "unknown"),
            team_size=data.get("teamSize", 0),
            attacks_per_member=data.get("attacksPerMember", 2),
            preparation_start=data.get("preparationStartTime", ""),
            start_time=data.get("startTime", ""),
            end_time=data.get("endTime", ""),
            clan=parse_clan_full(data.get("clan", {})),
            opponent=parse_opponent(data.get("opponent", {})),
        )

    ###########################################################################
    ### WAR — FORMATTING
    ###########################################################################

    def _fmt_discord_time(self, t: str, style: str) -> str:
        try:
            dt = datetime.strptime(t, "%Y%m%dT%H%M%S.%fZ")
            unix = int(dt.timestamp())
            return f"<t:{unix}:{style}>"
        except ValueError:
            return t

    def _format_war(self, war: WarState) -> tuple[str, str]:
        our_stats = f"⭐{war.clan.stars}  {war.clan.attacks}  {war.clan.destruction:.1f}%"
        opp_stats = f"⭐{war.opponent.stars}  {war.opponent.attacks}  {war.opponent.destruction:.1f}%"

        # Raw text footer — plain Discord message with timestamps
        raw_footer = (
            f"## **{war.clan.name}** vs **{war.opponent.name}**\n"
            f"### {our_stats}  |  {opp_stats}\n"
            f"# Prep: {self._fmt_discord_time(war.preparation_start, 'R')}"
            f"  ---  Start: {self._fmt_discord_time(war.start_time, 'R')}"
            f"  ---  End: {self._fmt_discord_time(war.end_time, 'f')} / {self._fmt_discord_time(war.end_time, 'R')}"
        )

        # Attack lines — one per member, sorted by map position
        def fmt_attack(a: dict) -> str:
            stars = a.get("stars", 0)
            pct   = a.get("destructionPercentage", 0)
            return f"{stars}⭐ {pct}%"

        lines = []
        for m in war.clan.members:
            attacks_str = "  ".join(fmt_attack(a) for a in m.attacks) if m.attacks else "no attacks"
            lines.append(
                f"\033[1;33m{m.name}\033[0m: {attacks_str}  |  {m.opponent_attacks}"
            )

        body = "```ansi\n" + "\n".join(lines) + "\n```"

        return body, raw_footer

    def _chunk_war(self, war: WarState) -> list[str]:
        ansi_body, raw_footer = self._format_war(war)

        inner = ansi_body
        if inner.startswith("```ansi\n"):
            inner = inner[len("```ansi\n"):]
        if inner.endswith("\n```"):
            inner = inner[:-len("\n```")]

        lines = inner.split("\n")
        chunks: list[str] = []
        current_lines: list[str] = []
        overhead = 8

        for line in lines:
            if overhead + sum(len(l) + 1 for l in current_lines) + len(line) + 1 > 1990:
                chunks.append("```ansi\n" + "\n".join(current_lines) + "\n```")
                current_lines = [line]
            else:
                current_lines.append(line)

        if current_lines:
            chunks.append("```ansi\n" + "\n".join(current_lines) + "\n```")

        # Raw footer is always the last message
        chunks.append(raw_footer)
        return chunks

    ###########################################################################
    ### WAR — POSTING
    ###########################################################################

    async def _fetch_and_post_war(self):
        channel = self.bot.get_channel(ANNOUNCE_ID)
        if not isinstance(channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)):
            return

        data = await self._fetch_war_data()
        if data is None:
            return

        state = data.get("state", "")
        if state in ("notInWar", "CLAN_NOT_FOUND", "ACCESS_DENIED"):
            chunks = [f"Not currently in war (state: `{state}`)"]
        else:
            war = self._parse_war(data)
            chunks = self._chunk_war(war)

        while len(self._war_message_ids) < len(chunks):
            self._war_message_ids.append(None)

        for i, content in enumerate(chunks):
            msg = None
            mid = self._war_message_ids[i]
            if mid is not None:
                try:
                    msg = await channel.fetch_message(mid)
                except discord.NotFound:
                    self._war_message_ids[i] = None

            if msg is None:
                msg = await channel.send(content)
                self._war_message_ids[i] = msg.id
            elif msg.content != content:
                await msg.edit(content=content)

        for i in range(len(chunks), len(self._war_message_ids)):
            mid = self._war_message_ids[i]
            if mid is not None:
                try:
                    msg = await channel.fetch_message(mid)
                    await msg.delete()
                except discord.NotFound:
                    pass
                self._war_message_ids[i] = None
        self._war_message_ids = self._war_message_ids[:len(chunks)]

    async def _war_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self._fetch_and_post_war()
            except Exception as e:
                print(f"[cocutils] war loop error: {e}")
            await asyncio.sleep(60)


###############################################################################
### SETUP
###############################################################################

async def setup(bot: Red):
    await bot.add_cog(CocUtils(bot))
