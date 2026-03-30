import os
import json
from .cwl import CwlCog
from redbot.core import commands
from redbot.core.bot import Red

from .rolelist import RoleListCog
from .war import WarCog


class CocUtils(commands.Cog):
    """Main cog — wires RoleList and War modules together."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.rolelist = RoleListCog(bot, self._save_state)
        self.war = WarCog(bot, self._save_state)
        self.cwl = CwlCog(bot, self._save_state)
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
                self.cwl._board_id = data.get("cwl_board_id")
                self.rolelist._message_ids = data.get("message_ids", [None, None])
                self.war._war_body_id = data.get("war_body_id")
                self.war._war_bangla_id = data.get("war_bangla_id")
                self.war._war_main_plain_id = data.get("war_main_plain_id")
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save_state(self):
        data = {
            "message_ids": self.rolelist._message_ids,
            "war_body_id": self.war._war_body_id,
            "war_bangla_id": self.war._war_bangla_id,
            "cwl_board_id": self.cwl._board_id,
            "war_main_plain_id": self.war._war_main_plain_id,
        }
        with open(self._state_path(), "w", encoding="utf-8") as f:
            json.dump(data, f)

    ###########################################################################
    ### LIFECYCLE
    ###########################################################################

    @commands.Cog.listener()
    async def on_ready(self):
        await self.rolelist.refresh()
        await self.war.start_loops()
        guild = self.bot.guilds[0] if self.bot.guilds else None
        if guild:
            await self.cwl.post_board(guild)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        await self.rolelist.on_member_update(before, after)

    ###########################################################################
    ### COMMAND DELEGATION
    ### All commands live in their respective modules and are registered here
    ### by adding them to the cog via get_commands.
    ###########################################################################

    @commands.is_owner()
    @commands.command()
    async def refresh_roles(self, ctx: commands.Context):
        await self.rolelist.refresh()
        await ctx.tick()

    @commands.is_owner()
    @commands.command()
    async def clanstat(self, ctx: commands.Context):
        await self.war.fetch_and_post_war()
        await ctx.tick()

    @commands.is_owner()
    @commands.command()
    async def clockrm(self, ctx: commands.Context):
        self.war._paused = True
        self.war._war_clocks.clear()
        self.war._notified.clear()
        self.war._war_body_id = None
        self.war._war_bangla_id = None
        self.war._war_main_plain_id = None
        self._save_state()
        await ctx.tick()

    @commands.is_owner()
    @commands.command()
    async def clockcount(self, ctx: commands.Context):
        await ctx.send(str(len(self.war._war_clocks)))

    @commands.is_owner()
    @commands.command()
    async def testping(self, ctx: commands.Context, window: str):
        await self.war.testping(ctx, window)

    @commands.is_owner()
    @commands.command()
    async def wardbg(self, ctx: commands.Context):
        await self.war.wardbg(ctx)

    @commands.is_owner()
    @commands.command()
    async def clockresume(self, ctx: commands.Context):
        self.war._paused = False
        await ctx.tick()

    @commands.is_owner()
    @commands.command()
    async def phasetest(self, ctx: commands.Context):
        await self.war.phasetest(ctx)

    @commands.is_owner()
    @commands.command()
    async def cwlupdate(self, ctx: commands.Context):
        """Fetch current CWL season and update the leaderboard."""
        status = await self.cwl.update()
        await ctx.send(status)
        guild = ctx.guild
        if guild:
            await self.cwl.post_board(guild)

    @commands.is_owner()
    @commands.command()
    async def cwlboard(self, ctx: commands.Context):
        """Manually refresh the CWL leaderboard post."""
        if ctx.guild:
            await self.cwl.post_board(ctx.guild)
        await ctx.tick()
