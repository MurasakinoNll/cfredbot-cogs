import os
import json
import unicodedata
from urllib.parse import quote
from dataclasses import dataclass

import aiohttp
import discord
from redbot.core import commands

from .constants import CLASH_APIKEY, CLAN_ID

CWL_BOARD_CHANNEL_ID = 1488252765586198688


###############################################################################
### DATACLASSES
###############################################################################


@dataclass
class CwlPlayerSeason:
    season: str
    wars: int
    attacks_used: int
    attacks_available: int
    total_stars: int
    total_destruction: float

    def score(self) -> float:
        """
        cwl ranking = ((stars * destruction%) / attacks^2) / 3
        Capped to 0-100 range.
        """
        if self.attacks_used == 0:
            return 0.0
        raw = ((self.total_stars * self.total_destruction) / (self.attacks_used**2)) / 3
        return min(raw, 100.0)


###############################################################################
### COG
###############################################################################


class CwlCog(commands.Cog):
    """CWL leaderboard — fetches, stores, and displays CWL performance."""

    def __init__(self, bot, state_save_fn):
        self.bot = bot
        self._save_state = state_save_fn
        self._board_id: int | None = None

    ###########################################################################
    ### FILE PATHS
    ###########################################################################

    def _cwl_path(self) -> str:
        return os.path.join(os.path.dirname(__file__), "cwl.json")

    def _data_path(self) -> str:
        return os.path.join(os.path.dirname(__file__), "data.json")

    ###########################################################################
    ### JSON I/O
    ###########################################################################

    def load_cwl(self) -> dict:
        try:
            with open(self._cwl_path(), "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_cwl(self, data: dict):
        with open(self._cwl_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load_data(self) -> dict:
        try:
            with open(self._data_path(), "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    ###########################################################################
    ### API
    ###########################################################################

    async def _get(self, url: str) -> dict | None:
        headers = {
            "Authorization": f"Bearer {CLASH_APIKEY}",
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    print(f"[cwl] API error {resp.status}: {url}")
                    return None
                return await resp.json()

    async def fetch_leaguegroup(self) -> dict | None:
        encoded = quote(CLAN_ID, safe="")
        return await self._get(
            f"https://api.clashofclans.com/v1/clans/{encoded}/currentwar/leaguegroup"
        )

    async def fetch_war(self, war_tag: str) -> dict | None:
        encoded = quote(war_tag, safe="")
        return await self._get(
            f"https://api.clashofclans.com/v1/clanwarleagues/wars/{encoded}"
        )

    ###########################################################################
    ### UPDATE
    ###########################################################################

    async def update(self) -> str:
        """Fetch current CWL season and update cwl.json. Returns a status string."""
        group = await self.fetch_leaguegroup()
        if group is None:
            return "Failed to fetch leaguegroup."

        state = group.get("state", "")
        if state not in ("inWar", "warEnded", "preparation"):
            return f"No active CWL season (state: `{state}`)."

        season = group.get("season", "unknown")
        rounds = group.get("rounds", [])
        war_tags = [tag for r in rounds for tag in r.get("warTags", []) if tag != "#0"]

        if not war_tags:
            return "No war tags found in leaguegroup."

        # player_tag -> {stars, destruction, attacks_used, attacks_available}
        aggregates: dict[str, dict] = {}

        for war_tag in war_tags:
            war = await self.fetch_war(war_tag)
            if war is None:
                continue

            clan_side = None
            if war.get("clan", {}).get("tag") == CLAN_ID:
                clan_side = war["clan"]
            elif war.get("opponent", {}).get("tag") == CLAN_ID:
                clan_side = war["opponent"]

            if clan_side is None:
                continue

            war_state = war.get("state", "")
            for member in clan_side.get("members", []):
                tag = member.get("tag", "")
                attacks = member.get("attacks", [])

                if tag not in aggregates:
                    aggregates[tag] = {
                        "name": member.get("name", ""),
                        "stars": 0,
                        "destruction": 0.0,
                        "attacks_used": 0,
                        "attacks_available": 0,
                    }

                # Only count attacks_available if war is over
                if war_state in ("warEnded",):
                    aggregates[tag]["attacks_available"] += 1

                for atk in attacks:
                    aggregates[tag]["stars"] += atk.get("stars", 0)
                    aggregates[tag]["destruction"] += atk.get(
                        "destructionPercentage", 0.0
                    )
                    aggregates[tag]["attacks_used"] += 1

        if not aggregates:
            return "No member data found across CWL wars."

        cwl_data = self.load_cwl()

        for tag, agg in aggregates.items():
            if tag not in cwl_data:
                cwl_data[tag] = {"name": agg["name"], "seasons": []}

            cwl_data[tag]["name"] = agg["name"]

            # Overwrite existing entry for this season or append new
            seasons = cwl_data[tag]["seasons"]
            existing = next((s for s in seasons if s["season"] == season), None)
            entry = {
                "season": season,
                "wars": len(war_tags),
                "attacks_used": agg["attacks_used"],
                "attacks_available": agg["attacks_available"],
                "total_stars": agg["stars"],
                "total_destruction": agg["destruction"],
            }
            if existing:
                idx = seasons.index(existing)
                seasons[idx] = entry
            else:
                seasons.append(entry)

        self.save_cwl(cwl_data)
        return f"CWL data updated for season `{season}` — {len(aggregates)} players recorded."

    ###########################################################################
    ### SCORING
    ###########################################################################

    def latest_season_score(self, seasons: list[dict]) -> tuple[float, dict] | None:
        """Return (score, season_dict) for the most recent season, or None."""
        if not seasons:
            return None
        latest = max(seasons, key=lambda s: s["season"])
        p = CwlPlayerSeason(
            season=latest["season"],
            wars=latest["wars"],
            attacks_used=latest["attacks_used"],
            attacks_available=latest["attacks_available"],
            total_stars=latest["total_stars"],
            total_destruction=latest["total_destruction"],
        )
        return p.score(), latest

    def clan_average_score(self, cwl_data: dict, discord_tags: set[str]) -> float:
        """Average score across all Discord-linked players with data."""
        scores = []
        for tag in discord_tags:
            entry = cwl_data.get(tag)
            if not entry:
                continue
            result = self.latest_season_score(entry["seasons"])
            if result:
                scores.append(result[0])
        return sum(scores) / len(scores) if scores else 0.0

    ###########################################################################
    ### FORMATTING
    ###########################################################################

    def safe_name(self, name: str) -> str:
        return f"\u202a{name}\u202c"

    def display_width(self, text: str) -> int:
        clean = text.replace("\u202a", "").replace("\u202c", "")
        return len(clean)

    def build_board(self, guild: discord.Guild) -> str:
        data_json = self.load_data()
        cwl_data = self.load_cwl()

        # Build tag -> discord member map
        tag_to_member: dict[str, discord.Member] = {}
        for discord_id, entry in data_json.items():
            if not isinstance(entry, dict):
                continue
            tag = entry.get("tag")
            if not tag:
                continue
            member = guild.get_member(int(discord_id))
            if member:
                tag_to_member[tag] = member

        discord_tags = set(tag_to_member.keys())
        avg = self.clan_average_score(cwl_data, discord_tags)

        # Build ranked list — omit players with no data
        ranked: list[tuple[float, str, discord.Member, dict]] = []
        for tag, member in tag_to_member.items():
            entry = cwl_data.get(tag)
            if not entry:
                continue
            result = self.latest_season_score(entry["seasons"])
            if result is None:
                continue
            score, season = result
            ranked.append((score, tag, member, season))

        ranked.sort(key=lambda x: x[0], reverse=True)

        if not ranked:
            return "```ansi\nNo CWL data available.\n```"

        max_name_len = max(
            (
                self.display_width(self.safe_name(m.display_name))
                for _, _, m, _ in ranked
            ),
            default=0,
        )

        lines = []
        for i, (score, tag, member, season) in enumerate(ranked, 1):
            name = self.safe_name(member.display_name)
            emoji_count = sum(1 for c in name if unicodedata.category(c) == "So")
            pad = max_name_len - self.display_width(name) - emoji_count
            arrow = "\033[1;32m↑\033[0m" if score >= avg else "\033[1;31m↓\033[0m"
            rank_color = "1;33" if i <= 3 else "1;37"
            stats = (
                f"{season['total_stars']}⭐ "
                f"{season['total_destruction']:.0f}% "
                f"{season['attacks_used']}/{season['attacks_available']} atk "
                f"| {score:.1f}pts"
            )
            lines.append(
                f"\033[{rank_color}m{i:2}. {name}\033[0m{' ' * pad} {arrow} {stats}"
            )

        season_label = ranked[0][3]["season"] if ranked else "unknown"
        header = f"CWL Leaderboard — {season_label} | clan avg: {avg:.1f}pts"
        return f"**{header}**\n```ansi\n" + "\n".join(lines) + "\n```"

    ###########################################################################
    ### POSTING
    ###########################################################################

    async def post_board(self, guild: discord.Guild):
        channel = self.bot.get_channel(CWL_BOARD_CHANNEL_ID)
        if not isinstance(
            channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)
        ):
            return

        content = self.build_board(guild)

        if self._board_id is not None:
            try:
                msg = await channel.fetch_message(self._board_id)
                if msg.content != content:
                    await msg.edit(content=content)
                return
            except discord.NotFound:
                self._board_id = None

        msg = await channel.send(content)
        self._board_id = msg.id
        self._save_state()
