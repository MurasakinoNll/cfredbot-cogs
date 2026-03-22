import unicodedata
import os
import json
import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import aiohttp
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
BANGLA_ID = "#2YP2PC8PJ"
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


@dataclass
class WarClock:
    prep_start: datetime
    war_start: datetime
    war_end: datetime
    queue_start: datetime
    next_prep_start: datetime

    @classmethod
    def from_war(cls, war: WarState) -> "WarClock":
        def p(t: str) -> datetime:
            return datetime.strptime(t, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=None)

        war_end = p(war.end_time)
        queue_start = war_end + timedelta(hours=1)
        next_prep = queue_start + timedelta(hours=1)
        return cls(
            prep_start=p(war.preparation_start),
            war_start=p(war.start_time),
            war_end=war_end,
            queue_start=queue_start,
            next_prep_start=next_prep,
        )

    def current_phase(self, now: datetime) -> str:
        if now < self.war_start:
            return "preparation"
        if now < self.war_end:
            return "war"
        if now < self.queue_start:
            return "cooldown"
        if now < self.next_prep_start:
            return "queue"
        return "ended"

    def next_queue_str(self) -> str:
        unix = int(self.queue_start.timestamp())
        return f"<t:{unix}:R> (<t:{unix}:f>)"


###############################################################################
### COG
###############################################################################


class CocUtils(commands.Cog):
    """Displays and auto-updates a list of members with specific roles."""

    def __init__(self, bot: Red):
        self.bot = bot
        self._message_ids: list[int | None] = [None, None]
        self._war_body_id: int | None = None
        self._war_bangla_id: int | None = None
        self._war_main_plain_id: int | None = None
        self._war_task: asyncio.Task | None = None
        self._war_clocks: dict[str, WarClock] = {}
        self._clock_task: asyncio.Task | None = None
        self._notified: set[str] = set()
        self._load_state()

    ###########################################################################
    ### STATE PERSISTENCE
    ###########################################################################

    def _state_path(self) -> str:
        return os.path.join(os.path.dirname(__file__), "state.json")

    def _load_state(self):
        try:
            with open(self._state_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
                self._message_ids = data.get("message_ids", [None, None])
                self._war_body_id = data.get("war_body_id")
                self._war_bangla_id = data.get("war_bangla_id")
                self._war_main_plain_id = data.get("war_main_plain_id")
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save_state(self):
        data = {
            "message_ids": self._message_ids,
            "war_body_id": self._war_body_id,
            "war_bangla_id": self._war_bangla_id,
            "war_main_plain_id": self._war_main_plain_id,
        }
        with open(self._state_path(), "w", encoding="utf-8") as f:
            json.dump(data, f)

    ###########################################################################
    ### LIFECYCLE
    ###########################################################################

    @commands.Cog.listener()
    async def on_ready(self):
        await self._refresh()
        if self._war_task is None or self._war_task.done():
            self._war_task = asyncio.ensure_future(self._war_loop())
        if self._clock_task is None or self._clock_task.done():
            self._clock_task = asyncio.ensure_future(self._clock_loop())

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
        await self._refresh()
        await ctx.tick()

    @commands.is_owner()
    @commands.command()
    async def clanstat(self, ctx: commands.Context):
        await self._fetch_and_post_war()
        await ctx.tick()

    @commands.is_owner()
    @commands.command()
    async def clockrm(self, ctx: commands.Context):
        """Clear all active war clocks, notification flags, and cached message IDs."""
        self._war_clocks.clear()
        self._notified.clear()
        self._war_body_id = None
        self._war_bangla_id = None
        self._war_main_plain_id = None
        self._save_state()
        await ctx.tick()

    @commands.is_owner()
    @commands.command()
    async def wardbg(self, ctx: commands.Context):
        now = datetime.now(UTC).replace(tzinfo=None)
        lines = [f"UTC now: `{now.strftime('%Y-%m-%d %H:%M:%S')}`"]
        if not self._war_clocks:
            lines.append("No war clocks loaded.")
        for clan_id, clock in self._war_clocks.items():
            phase = clock.current_phase(now)
            secs_to_queue = (clock.queue_start - now).total_seconds()
            secs_to_end = (clock.war_end - now).total_seconds()
            lines.append(
                f"\n**{clan_id}**"
                f"\n  phase: `{phase}`"
                f"\n  war ends in: `{secs_to_end:.0f}s` ({secs_to_end / 3600:.2f}h)"
                f"\n  queue opens in: `{secs_to_queue:.0f}s` ({secs_to_queue / 3600:.2f}h)"
                f"\n  queue at: `{clock.queue_start.strftime('%Y-%m-%d %H:%M:%S')} UTC`"
                f"\n  notified flags: `{[k for k in self._notified if k.startswith(clan_id)]}`"
            )
        await ctx.send("\n".join(lines))

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

    def _build_block(
        self,
        guild: discord.Guild,
        members: list[discord.Member],
        role_id: int | None,
        color: str,
        max_len: int,
        altnames: dict[str, str],
    ) -> str:
        header = (
            f"<@&{role_id}>"
            if role_id
            else "<@&1483520189902356641> and <@&1483519620018077918>"
        )
        if not members:
            return f"**{header}:**\n```ansi\nNo members\n```"

        lines = []
        for m in members:
            name = self._safe_name(m.display_name)
            emoji_count = sum(1 for char in name if unicodedata.category(char) == "So")
            pad = max_len - self._display_width(name) - emoji_count
            alt = altnames.get(str(m.id), "unknown IGN")
            lines.append(
                f"\033[{color}m{name}{' ' * pad}\033[0m | \033[1;37m{alt}\033[0m"
            )

        member_list = "\n".join(lines)
        return f"**{header}:**\n```ansi\n{member_list}\n```"

    def _build_contents(
        self, guild: discord.Guild, altnames: dict[str, str]
    ) -> tuple[str, str]:
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
            default=0,
        )

        block1 = self._build_block(
            guild, members1, ROLE_IDS[0], "1;33", max_len, altnames
        )
        block2 = self._build_block(
            guild, members2, ROLE_IDS[1], "1;34", max_len, altnames
        )
        block3 = self._build_block(
            guild, members3, ROLE_IDS[2], "1;31", max_len, altnames
        )
        block4 = self._build_block(guild, members4, None, "1;36", max_len, altnames)

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
                self._save_state()
            elif msg.content != content:
                await msg.edit(content=content)

    ###########################################################################
    ### WAR — API
    ###########################################################################

    async def _fetch_war_data(self, clan_id: str) -> dict | None:
        encoded = quote(clan_id, safe="")
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
                result.append(
                    WarMember(
                        tag=m.get("tag", ""),
                        name=m.get("name", ""),
                        townhall_level=m.get("townhallLevel", 0),
                        map_position=m.get("mapPosition", 0),
                        opponent_attacks=m.get("opponentAttacks", 0),
                        attacks=m.get("attacks", []),
                        best_opponent_attack=m.get("bestOpponentAttack"),
                    )
                )
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
                for m in sorted(
                    c.get("members", []), key=lambda m: m.get("mapPosition", 0)
                )
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

    def _format_plain(self, war: WarState, clock: WarClock | None = None) -> str:
        our_stats = (
            f"⭐{war.clan.stars}  {war.clan.attacks}  {war.clan.destruction:.1f}%"
        )
        opp_stats = f"⭐{war.opponent.stars}  {war.opponent.attacks}  {war.opponent.destruction:.1f}%"
        queue_line = (
            f"\nNext war starting in {clock.next_queue_str()}"
            if clock is not None
            else ""
        )
        return (
            f" **{war.clan.name}** vs **{war.opponent.name}**\n"
            f" **{our_stats}  |  {opp_stats}**\n"
            f"## Prep: {self._fmt_discord_time(war.preparation_start, 'f')}"
            f"  ---  Start: {self._fmt_discord_time(war.start_time, 'R')}"
            f"  ---  End: {self._fmt_discord_time(war.end_time, 'f')} / {self._fmt_discord_time(war.end_time, 'R')}"
            f"# {queue_line}\n"
            "------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------\n"
        )

    def _format_body(self, war: WarState) -> str:
        def fmt_attack(a: dict) -> str:
            stars = a.get("stars", 0)
            pct = a.get("destructionPercentage", 0)
            return f"{stars}⭐ {pct}%"

        max_len = max((len(m.name) for m in war.clan.members), default=0)
        atk_max = max(
            (
                len("  ".join(fmt_attack(a) for a in m.attacks))
                if m.attacks
                else len(" - ")
                for m in war.clan.members
            ),
            default=0,
        )

        lines = []
        for m in war.clan.members:
            attacks_str = (
                "  ".join(fmt_attack(a) for a in m.attacks) if m.attacks else " - "
            )
            pad = max_len - len(m.name)
            atk_pad = atk_max - len(attacks_str)
            lines.append(
                f"\033[1;36m{m.name}\033[0m{' ' * pad}: {attacks_str}{' ' * atk_pad} --- {m.opponent_attacks} defended"
            )

        return "```ansi\n" + "\n".join(lines) + "\n```"

    ###########################################################################
    ### WAR CLOCK — LOOP
    ###########################################################################

    async def _clock_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self._tick_clock()
            except Exception as e:
                print(f"[cocutils] clock loop error: {e}")
            await asyncio.sleep(60)

    async def _tick_clock(self):
        now = datetime.now(UTC).replace(tzinfo=None)
        for clan_id, clock in self._war_clocks.items():
            phase = clock.current_phase(now)

            if phase == "preparation" and f"{clan_id}:reset" not in self._notified:
                self._notified = {
                    k for k in self._notified if not k.startswith(clan_id)
                }
                self._notified.add(f"{clan_id}:reset")

            secs_to_queue = (clock.queue_start - now).total_seconds()

            if (
                phase in ("cooldown", "war")
                and secs_to_queue <= 3600
                and f"{clan_id}:1h" not in self._notified
            ):
                self._notified.add(f"{clan_id}:1h")
                await self._on_queue_approaching("1h", clock)

            if (
                phase in ("cooldown", "war")
                and secs_to_queue <= 1800
                and f"{clan_id}:30m" not in self._notified
            ):
                self._notified.add(f"{clan_id}:30m")
                await self._on_queue_approaching("30m", clock)

            if (
                phase in ("cooldown", "war")
                and secs_to_queue <= 300
                and f"{clan_id}:5m" not in self._notified
            ):
                self._notified.add(f"{clan_id}:5m")
                await self._on_queue_approaching("5m", clock)

            if phase == "queue" and f"{clan_id}:war_queue" not in self._notified:
                self._notified.add(f"{clan_id}:war_queue")
                await self._on_war_queue(clock)

            if phase == "ended" and f"{clan_id}:war_end" not in self._notified:
                self._notified.add(f"{clan_id}:war_end")
                await self._on_war_end(clock)

    ###########################################################################
    ### WAR CLOCK — EVENT HOOKS
    ###########################################################################

    async def _on_queue_approaching(self, window: str, clock: WarClock):
        pass

    async def _on_war_queue(self, clock: WarClock):
        pass

    async def _on_war_end(self, clock: WarClock):
        pass

    ###########################################################################
    ### WAR — POSTING
    ###########################################################################

    async def _find_existing(
        self,
        channel: discord.TextChannel | discord.Thread | discord.VoiceChannel,
        marker: str,
    ) -> int | None:
        async for msg in channel.history(limit=50):
            if msg.author == self.bot.user and marker in msg.content:
                return msg.id
        return None

    async def _edit_or_send(
        self,
        channel: discord.TextChannel | discord.Thread | discord.VoiceChannel,
        mid: int | None,
        content: str,
        marker: str,
    ) -> int:
        if mid is None:
            mid = await self._find_existing(channel, marker)

        if mid is not None:
            try:
                msg = await channel.fetch_message(mid)
                if msg.content != content:
                    await msg.edit(content=content)
                return msg.id
            except discord.NotFound:
                pass

        msg = await channel.send(content)
        return msg.id

    async def _fetch_and_post_war(self):
        channel = self.bot.get_channel(ANNOUNCE_ID)
        if not isinstance(
            channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)
        ):
            return

        main_data = await self._fetch_war_data(CLAN_ID)
        bangla_data = await self._fetch_war_data(BANGLA_ID)

        main_body: str | None = None
        main_plain: str | None = None
        if main_data and main_data.get("state", "") not in (
            "notInWar",
            "CLAN_NOT_FOUND",
            "ACCESS_DENIED",
        ):
            main_war = self._parse_war(main_data)
            self._war_clocks[CLAN_ID] = WarClock.from_war(main_war)
            main_body = self._format_body(main_war)
            main_plain = self._format_plain(main_war, self._war_clocks[CLAN_ID])
        elif main_data:
            main_plain = (
                f"Main clan not in war (state: `{main_data.get('state', 'unknown')}`)"
            )

        bangla_plain: str | None = None
        if bangla_data and bangla_data.get("state", "") not in (
            "notInWar",
            "CLAN_NOT_FOUND",
            "ACCESS_DENIED",
        ):
            bangla_war = self._parse_war(bangla_data)
            self._war_clocks[BANGLA_ID] = WarClock.from_war(bangla_war)
            bangla_plain = self._format_plain(bangla_war, self._war_clocks[BANGLA_ID])
        elif bangla_data:
            bangla_plain = f"Bangla clan not in war (state: `{bangla_data.get('state', 'unknown')}`)"

        if main_body:
            self._war_body_id = await self._edit_or_send(
                channel, self._war_body_id, main_body, "```ansi"
            )
            self._save_state()

        if bangla_plain:
            self._war_bangla_id = await self._edit_or_send(
                channel, self._war_bangla_id, bangla_plain, BANGLA_ID
            )
            self._save_state()

        if main_plain:
            self._war_main_plain_id = await self._edit_or_send(
                channel, self._war_main_plain_id, main_plain, CLAN_ID
            )
            self._save_state()

    async def _war_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self._fetch_and_post_war()
            except Exception as e:
                print(f"[cocutils] war loop error: {e}")
            await asyncio.sleep(60)
