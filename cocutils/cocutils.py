import unicodedata
import os
import json
import discord
import aiohttp
from urllib.parse import quote
from redbot.core import commands
from redbot.core.bot import Red
import asyncio
from dataclasses import dataclass, field
from datetime import datetime

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
CHANNEL_ID = 1483917391980396605
ANNOUNCE_ID = 1484351000788598927
ROLE_IDS = [
    1235277600151179364,
    1483520189902356641,
    1483519620018077918,
]

CLAN_ID = "#2YUYR2LPV"
CLASH_APIKEY = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiIsImtpZCI6IjI4YTMxOGY3LTAwMDAtYTFlYi03ZmExLTJjNzQzM2M2Y2NhNSJ9.eyJpc3MiOiJzdXBlcmNlbGwiLCJhdWQiOiJzdXBlcmNlbGw6Z2FtZWFwaSIsImp0aSI6Ijg2N2E4N2Q4LTRiZGYtNDI3NC1hOWUyLWNjNDFkODNiZGVmMiIsImlhdCI6MTc3Mzk2OTE1MSwic3ViIjoiZGV2ZWxvcGVyLzZjOGMxYmRjLWI3ODQtMjA5Ny0wOGQ5LTdiMWEzYTM2Y2Y2MCIsInNjb3BlcyI6WyJjbGFzaCJdLCJsaW1pdHMiOlt7InRpZXIiOiJkZXZlbG9wZXIvc2lsdmVyIiwidHlwZSI6InRocm90dGxpbmcifSx7ImNpZHJzIjpbIjE3Ni4yOC4xNDkuMjUyIiwiNS45NS4yNTAuNzEiXSwidHlwZSI6ImNsaWVudCJ9XX0.4A7gfJGrghRbetI6bFN1A8rJE9GOex2hV45oSv0xNJekKup4AEmnaa5rjfN0rjociaoXXH2qTDEc-wbbn4GPAQ"
class CocUtils(commands.Cog):
    """Displays and auto-updates a list of members with specific roles."""

    def __init__(self, bot: Red):
        self.bot = bot
        self._message_ids: list[int | None] = [None, None]
        self._war_message_id: int | None = None
        self._war_task: asyncio.Task | None = None

    ############################# WAR PARSING ################################

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

    ############################# ^^ WAR PARSING ################################

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
    @commands.command
    @commands.is_owner()
    async def refresh_roles(self, ctx: commands.Context):
        """Manually trigger a role-list refresh."""
        await self._refresh()
        await ctx.tick()

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

    async def _fetch_and_post_war(self):
        channel = self.bot.get_channel(ANNOUNCE_ID)
        if not isinstance(channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)):
            return

        data = await self._fetch_war_data()
        if data is None:
            return

        state = data.get("state", "")
        if state in ("notInWar", "CLAN_NOT_FOUND", "ACCESS_DENIED"):
            content = f"Not currently in war (state: `{state}`)"
        else:
            war = self._parse_war(data)
            content = self._format_war(war)  # you define the format

        # Chunk if needed — persistent single msg per chunk
        chunks = [content[i:i+1990] for i in range(0, len(content), 1990)]

        # For now just handle single message (extend to list if needed)
        msg = None
        if self._war_message_id:
            try:
                msg = await channel.fetch_message(self._war_message_id)
            except discord.NotFound:
                self._war_message_id = None

        if msg is None:
            msg = await channel.send(chunks[0])
            self._war_message_id = msg.id
        elif msg.content != chunks[0]:
            await msg.edit(content=chunks[0])
    
    def _format_war(self, war: WarState) -> str:
            # -- name header --
        our  = war.clan.name
        opp  = war.opponent.name
        total_width = 80
        vs = "vs"
        left_pad  = (total_width // 2) - len(our) - len(vs) // 2
        right_pad = total_width - len(our) - len(opp) - len(vs) - left_pad
        header = f"\033[1;33m{our}\033[0m{' ' * left_pad}{vs}{' ' * right_pad}\033[1;31m{opp}\033[0m formatting by gippity:tm:"

        # -- stats row --
        our_stats  = f"⭐{war.clan.stars}  ⚔️{war.clan.attacks}  💥{war.clan.destruction:.1f}%"
        opp_stats  = f"⭐{war.opponent.stars}  ⚔️{war.opponent.attacks}  💥{war.opponent.destruction:.1f}%"
        stats_pad  = total_width - len(our_stats) - len(opp_stats)
        stats_row  = f"\033[1;33m{our_stats}\033[0m{' ' * stats_pad}\033[1;31m{opp_stats}\033[0m"

        # -- times --
        def fmt_time(t: str) -> str:
            # "20260320T163357.000Z" -> "2026-03-20 16:33"
            try:
                dt = datetime.strptime(t, "%Y%m%dT%H%M%S.%fZ")
                return dt.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                return t

        times = (
            f"\033[0;37mPrep: {fmt_time(war.preparation_start)}"
            f"   Start: {fmt_time(war.start_time)}"
            f"   End: {fmt_time(war.end_time)}\033[0m"
        )

        # -- member formatter --
        def fmt_member(m: WarMember, color: str) -> list[str]:
            stars  = m.attacks[0].get("stars", 0) if m.attacks else 0
            destro = m.attacks[0].get("destructionPercentage", 0) if m.attacks else 0
            lines  = [
                f"\033[{color}m{m.name}\033[0m",
                f"  ⭐ {stars}",
                f"  💥 {destro}%",
                f"  📍 #{m.map_position}",
                f"  🏠 TH{m.townhall_level}",
            ]
            return lines

        # -- side by side member lists --
        our_lines = []
        for m in war.clan.members:
            our_lines += fmt_member(m, "1;33")
            our_lines.append("")  # spacer between members

        opp_lines = []
        for m in war.opponent.members:
            opp_lines += fmt_member(m, "1;31")
            opp_lines.append("")

        # Pad both sides to same length then zip
        max_lines = max(len(our_lines), len(opp_lines))
        our_lines += [""] * (max_lines - len(our_lines))
        opp_lines += [""] * (max_lines - len(opp_lines))

        col_width = total_width // 2
        member_rows = []
        for left, right in zip(our_lines, opp_lines):
            # Strip ANSI for length calc
            import re
            ansi_escape = re.compile(r'\033\[[0-9;]*m')
            left_visual  = len(ansi_escape.sub('', left))
            pad = col_width - left_visual
            member_rows.append(f"{left}{' ' * pad}{right}")

        members_block = "\n".join(member_rows)

        return (
            f"```ansi\n"
            f"{header}\n"
            f"{stats_row}\n"
            f"{times}\n"
            f"{'─' * total_width}\n"
            f"{members_block}\n"
            f"```"
        )
        # ---- define your format here ----
        return f"war state: {war.state}\n[format TBD]"

    async def _war_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self._fetch_and_post_war()
            except Exception as e:
                print(f"[cocutils] war loop error: {e}")
            await asyncio.sleep(60)

    @commands.is_owner()
    @commands.command()
    async def clanstat(self, ctx: commands.Context):
        await self._fetch_and_post_war()
        await ctx.tick()
        # encodedurlid = quote(CLAN_ID, safe="")
        # url = f"https://api.clashofclans.com/v1/clans/{encodedurlid}/currentwar"
        # header = {"Authorization": f"Bearer {CLASH_APIKEY}"}
        #
        # async with aiohttp.ClientSession() as session:
        #     async with session.get(url, headers=header, ssl=False) as response:
        #         if response.status != 200:
        #             await ctx.send(f"coc api shat itself: {response.status}. \n\n```DEBUG {encodedurlid}```  \n```uri:{url}``` \n```header:{header}``` \n \n ```resp: {response}```")
        #             return
        #         data = await response.json()
        #
        # out = json.dumps(data, indent=2)
        # announcechannel = self.bot.get_channel(ANNOUNCE_ID)
        # if not isinstance(announcechannel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)):
        #     await ctx.send("announce channel not found.")
        #     return
        # chunk_size = 1690
        # chunks = [out[i:i+chunk_size] for i in range(0, len(out), chunk_size)]
        # for chunk in chunks:
        #     await announcechannel.send(f"```json\n{chunk}\n```")
