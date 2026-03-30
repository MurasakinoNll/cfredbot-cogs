import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from urllib.parse import quote

import aiohttp
import discord
import calendar
from redbot.core import commands

from .constants import (
    ANNOUNCE_ID,
    BANGLA_ID,
    CLAN_ID,
    CLASH_APIKEY,
    PING_CHANNEL_ID,
    PING_ROLE_ID,
)


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
        unix = int(self.queue_start.replace(tzinfo=timezone.utc).timestamp())
        return f"<t:{unix}:R> (<t:{unix}:f>)"


###############################################################################
### COG
###############################################################################


class WarCog(commands.Cog):
    """War status display and clock management."""

    def __init__(self, bot, state_save_fn):
        self.bot = bot
        self._save_state = state_save_fn
        self._war_body_id: int | None = None
        self._war_bangla_id: int | None = None
        self._war_main_plain_id: int | None = None
        self._war_task: asyncio.Task | None = None
        self._clock_task: asyncio.Task | None = None
        self._war_clocks: dict[str, WarClock] = {}
        self._notified: set[str] = set()
        self._paused: bool = False

    ###########################################################################
    ### LIFECYCLE
    ###########################################################################

    async def start_loops(self):
        if self._war_task is None or self._war_task.done():
            self._war_task = asyncio.ensure_future(self._war_loop())
        if self._clock_task is None or self._clock_task.done():
            self._clock_task = asyncio.ensure_future(self._clock_loop())

    def stop_loops(self):
        if self._war_task:
            self._war_task.cancel()
        if self._clock_task:
            self._clock_task.cancel()

    ###########################################################################
    ### COMMANDS
    ###########################################################################

    async def clanstat(self, ctx: commands.Context):
        await self.fetch_and_post_war()
        await ctx.tick()

    async def clockrm(self, ctx: commands.Context):
        """Clear all active war clocks, notification flags, and cached war message IDs."""
        self._war_clocks.clear()
        self._notified.clear()
        self._war_body_id = None
        self._war_bangla_id = None
        self._war_main_plain_id = None
        self._save_state()
        await ctx.tick()

    async def clockcount(self, ctx: commands.Context):
        await ctx.send(str(len(self._war_clocks)))

    async def testping(self, ctx: commands.Context, window: str):
        """Trigger a ping event manually. Args: 1h, 30m, 5m, queue, end"""
        if not self._war_clocks:
            await ctx.send("No war clocks loaded.")
            return
        clock = next(iter(self._war_clocks.values()))
        if window in ("1h", "30m", "5m"):
            await self._on_queue_approaching(window, clock)
        elif window == "queue":
            await self._on_war_queue(clock)
        elif window == "end":
            await self._on_war_end(clock)
        else:
            await ctx.send(f"Unknown window `{window}`. Use: 1h, 30m, 5m, queue, end")
            return
        await ctx.tick()

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
    ### API
    ###########################################################################

    async def fetch_war_data(self, clan_id: str) -> dict | None:
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
    ### PARSING
    ###########################################################################

    def parse_war(self, data: dict) -> WarState:
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
    ### FORMATTING
    ###########################################################################

    def fmt_discord_time(self, t: str, style: str) -> str:
        try:
            dt = datetime.strptime(t, "%Y%m%dT%H%M%S.%fZ")
            unix = int(dt.replace(tzinfo=timezone.utc).timestamp())
            return f"<t:{unix}:{style}>"
        except ValueError:
            return t

    def format_plain(self, war: WarState, clock: WarClock | None = None) -> str:
        our_stats = (
            f"⭐{war.clan.stars}  {war.clan.attacks}  {war.clan.destruction:.1f}%"
        )
        opp_stats = f"⭐{war.opponent.stars}  {war.opponent.attacks}  {war.opponent.destruction:.1f}%"
        queue_line = f"\nNext war starting in {clock.next_queue_str()}" if clock else ""
        return (
            "──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────\n"
            f" **{war.clan.name}** vs **{war.opponent.name}**\n"
            f" **{our_stats}  |  {opp_stats}**\n"
            f"## Prep: {self.fmt_discord_time(war.preparation_start, 'R')}"
            f"  ──-  Start: {self.fmt_discord_time(war.start_time, 'R')}"
            f"  ──-  End: {self.fmt_discord_time(war.end_time, 'f')} / {self.fmt_discord_time(war.end_time, 'R')}"
            f"# {queue_line}\n"
            "──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────\n"
        )

    def format_body(self, war: WarState) -> str:
        def fmt_attacks(attacks: list[dict]) -> str:
            if not attacks:
                return " - "
            total_stars = sum(a.get("stars", 0) for a in attacks)
            total_pct = sum(a.get("destructionPercentage", 0) for a in attacks)
            return f"{total_stars}⭐ {total_pct}%"

        max_len = max((len(m.name) for m in war.clan.members), default=0)
        lines = []
        for m in war.clan.members:
            attacks_str = fmt_attacks(m.attacks)
            pad = max_len - len(m.name)
            lines.append(
                f"\033[1;36m{m.name}\033[0m{' ' * pad}: {attacks_str} ──- {m.opponent_attacks} defended"
            )
        return "```ansi\n" + "\n".join(lines) + "\n```"

    ###########################################################################
    ### CLOCK LOOP
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
        for clan_id, clock in list(self._war_clocks.items()):
            phase = clock.current_phase(now)
            secs_to_queue = (clock.queue_start - now).total_seconds()

            if phase == "preparation" and f"{clan_id}:reset" not in self._notified:
                self._notified = {
                    k for k in self._notified if not k.startswith(clan_id)
                }
                self._notified.add(f"{clan_id}:reset")

            # Approaching warnings — only fire during war or cooldown
            if phase in ("war", "cooldown"):
                if secs_to_queue <= 3600 and f"{clan_id}:1h" not in self._notified:
                    self._notified.add(f"{clan_id}:1h")
                    await self._on_queue_approaching("1h", clock)

                if secs_to_queue <= 1800 and f"{clan_id}:30m" not in self._notified:
                    self._notified.add(f"{clan_id}:30m")
                    await self._on_queue_approaching("30m", clock)

                if secs_to_queue <= 300 and f"{clan_id}:5m" not in self._notified:
                    self._notified.add(f"{clan_id}:5m")
                    await self._on_queue_approaching("5m", clock)

            # These fire once when the phase is first detected
            if phase == "cooldown" and f"{clan_id}:war_end" not in self._notified:
                self._notified.add(f"{clan_id}:war_end")
                await self._on_war_end(clock)

            if phase == "queue" and f"{clan_id}:war_queue" not in self._notified:
                self._notified.add(f"{clan_id}:war_queue")
                await self._on_war_queue(clock)

    ###########################################################################
    ### EVENT HOOKS
    ###########################################################################

    async def _on_queue_approaching(self, window: str, clock: WarClock):
        channel = self.bot.get_channel(PING_CHANNEL_ID)
        if not isinstance(
            channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)
        ):
            return
        messages = {
            "1h": f"<@&{PING_ROLE_ID}> war search in 1 hour {clock.next_queue_str()}",
            "5m": f"<@&{PING_ROLE_ID}> war search in 5 minutes {clock.next_queue_str()}",
        }
        msg = messages.get(window, "")
        if msg:
            await channel.send(
                msg, allowed_mentions=discord.AllowedMentions(roles=True)
            )

    async def _on_war_queue(self, clock: WarClock):
        channel = self.bot.get_channel(PING_CHANNEL_ID)
        if not isinstance(
            channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)
        ):
            return
        await channel.send(
            f"<@&{PING_ROLE_ID}> war search started",
            allowed_mentions=discord.AllowedMentions(roles=True),
        )

    async def _on_war_end(self, clock: WarClock):
        channel = self.bot.get_channel(PING_CHANNEL_ID)
        if not isinstance(
            channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)
        ):
            return
        await channel.send(
            f"<@&{PING_ROLE_ID}> war has ended — next search begins at: {clock.next_queue_str()}",
            allowed_mentions=discord.AllowedMentions(roles=True),
        )

    ###########################################################################
    ### POSTING
    ###########################################################################

    async def find_existing(self, channel, marker: str) -> int | None:
        async for msg in channel.history(limit=50):
            if msg.author == self.bot.user and marker in msg.content:
                return msg.id
        return None

    async def edit_or_send(
        self, channel, mid: int | None, content: str, marker: str
    ) -> int:
        if mid is None:
            mid = await self.find_existing(channel, marker)

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

    async def cleanup_channel(self, channel, keep_ids: list[int | None]) -> None:
        valid = {mid for mid in keep_ids if mid is not None}
        async for msg in channel.history(limit=100):
            if msg.author == self.bot.user and msg.id not in valid:
                try:
                    await msg.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass

    async def fetch_and_post_war(self):
        channel = self.bot.get_channel(ANNOUNCE_ID)
        if not isinstance(
            channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)
        ):
            return

        main_data = await self.fetch_war_data(CLAN_ID)
        bangla_data = await self.fetch_war_data(BANGLA_ID)

        main_body: str | None = None
        main_plain: str | None = None
        if main_data and main_data.get("state", "") not in (
            "notInWar",
            "CLAN_NOT_FOUND",
            "ACCESS_DENIED",
        ):
            main_war = self.parse_war(main_data)
            self._war_clocks[CLAN_ID] = WarClock.from_war(main_war)
            main_body = self.format_body(main_war)
            main_plain = self.format_plain(main_war, self._war_clocks[CLAN_ID])
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
            bangla_war = self.parse_war(bangla_data)
            self._war_clocks[BANGLA_ID] = WarClock.from_war(bangla_war)
            bangla_plain = self.format_plain(bangla_war, self._war_clocks[BANGLA_ID])
        elif bangla_data:
            bangla_plain = f"Bangla clan not in war (state: `{bangla_data.get('state', 'unknown')}`)"

        if main_body:
            self._war_body_id = await self.edit_or_send(
                channel, self._war_body_id, main_body, "```ansi"
            )
            self._save_state()

        if bangla_plain:
            self._war_bangla_id = await self.edit_or_send(
                channel, self._war_bangla_id, bangla_plain, BANGLA_ID
            )
            self._save_state()

        if main_plain:
            self._war_main_plain_id = await self.edit_or_send(
                channel, self._war_main_plain_id, main_plain, CLAN_ID
            )
            self._save_state()

        await self.cleanup_channel(
            channel, [self._war_body_id, self._war_bangla_id, self._war_main_plain_id]
        )

    async def _war_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            if not self._paused:
                try:
                    await self.fetch_and_post_war()
                except Exception as e:
                    print(f"[cocutils] war loop error: {e}")
            await asyncio.sleep(120)

    async def phasetest(self, ctx: commands.Context):
        if not self._war_clocks:
            await ctx.send("No war clocks loaded.")
            return

        now = datetime.now(UTC).replace(tzinfo=None)
        clock = next(iter(self._war_clocks.values()))

        time_to_war_end = clock.war_end - now

        fake_war_end = now + timedelta(seconds=30)
        fake_queue_start = fake_war_end + (clock.queue_start - clock.war_end)
        fake_next_prep = fake_war_end + (clock.next_prep_start - clock.war_end)

        fake_clock = WarClock(
            prep_start=fake_war_end - (clock.war_end - clock.prep_start),
            war_start=fake_war_end - (clock.war_end - clock.war_start),
            war_end=fake_war_end,
            queue_start=fake_queue_start,
            next_prep_start=fake_next_prep,
        )

        await ctx.send(
            f"  real war ends in `{time_to_war_end}` — testing with preserved offsets\n"
            f"  fake war end: <t:{int(calendar.timegm(fake_war_end.timetuple()))}:R>\n"
            f"  fake queue open: <t:{int(calendar.timegm(fake_queue_start.timetuple()))}:R>"
        )

        await ctx.send("testing war end...")
        await self._on_war_end(fake_clock)

        await ctx.send("testing 1h warning...")
        await self._on_queue_approaching("1h", fake_clock)

        await ctx.send("testing 5m warning...")
        await self._on_queue_approaching("5m", fake_clock)

        await ctx.send("testing war search start...")
        await self._on_war_queue(fake_clock)

        await ctx.send("phasetest complete")
