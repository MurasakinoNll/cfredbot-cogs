import unicodedata
import os
import json

import discord
from redbot.core import commands

from .constants import CHANNEL_ID, ROLE_IDS


class RoleListCog(commands.Cog):
    """Displays and auto-updates a list of members with specific roles."""

    def __init__(self, bot, state_save_fn):
        self.bot = bot
        self._save_state = state_save_fn
        self._message_ids: list[int | None] = [None, None]

    ###########################################################################
    ### LIFECYCLE
    ###########################################################################

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        before_ids = {r.id for r in before.roles}
        after_ids = {r.id for r in after.roles}
        if any(rid in (before_ids ^ after_ids) for rid in ROLE_IDS):
            await self.refresh()

    ###########################################################################
    ### COMMANDS
    ###########################################################################

    @commands.is_owner()
    @commands.command()
    async def refresh_roles(self, ctx: commands.Context):
        await self.refresh()
        await ctx.tick()

    ###########################################################################
    ### HELPERS
    ###########################################################################

    def load_altnames(self) -> dict[str, str]:
        path = os.path.join(os.path.dirname(__file__), "data.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
                # Handle both flat {"id": "name"} and new {"id": {"ign": ..., "tag": ...}}
                return {
                    k: (v["ign"] if isinstance(v, dict) else v) for k, v in raw.items()
                }
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def get_role_members(
        self, guild: discord.Guild, role_id: int
    ) -> list[discord.Member]:
        role = guild.get_role(role_id)
        return role.members if role else []

    def safe_name(self, name: str) -> str:
        return f"\u202a{name}\u202c"

    def display_width(self, text: str) -> int:
        clean = text.replace("\u202a", "").replace("\u202c", "")
        return len(clean)

    def build_block(
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
            name = self.safe_name(m.display_name)
            emoji_count = sum(1 for char in name if unicodedata.category(char) == "So")
            pad = max_len - self.display_width(name) - emoji_count
            alt = altnames.get(str(m.id), "unknown IGN")
            lines.append(
                f"\033[{color}m{name}{' ' * pad}\033[0m | \033[1;37m{alt}\033[0m"
            )

        return f"**{header}:**\n```ansi\n" + "\n".join(lines) + "\n```"

    def build_contents(
        self, guild: discord.Guild, altnames: dict[str, str]
    ) -> tuple[str, str]:
        members1 = self.get_role_members(guild, ROLE_IDS[0])
        members2 = self.get_role_members(guild, ROLE_IDS[1])
        members3 = self.get_role_members(guild, ROLE_IDS[2])

        ids2 = {m.id for m in members2}
        ids3 = {m.id for m in members3}
        members4 = [m for m in members2 if m.id in ids3]

        all_members = members1 + members2 + members3 + members4
        max_len = max(
            (self.display_width(self.safe_name(m.display_name)) for m in all_members),
            default=0,
        )

        block1 = self.build_block(
            guild, members1, ROLE_IDS[0], "1;33", max_len, altnames
        )
        block2 = self.build_block(
            guild, members2, ROLE_IDS[1], "1;34", max_len, altnames
        )
        block3 = self.build_block(
            guild, members3, ROLE_IDS[2], "1;31", max_len, altnames
        )
        block4 = self.build_block(guild, members4, None, "1;36", max_len, altnames)

        return f"{block1}\n{block2}", f"\n{block3}\n{block4}"

    async def fetch_or_none(self, channel: discord.TextChannel, msg_id: int | None):
        if msg_id is None:
            return None
        try:
            return await channel.fetch_message(msg_id)
        except discord.NotFound:
            return None

    async def cleanup_channel(self, channel: discord.TextChannel) -> None:
        valid = {mid for mid in self._message_ids if mid is not None}
        async for msg in channel.history(limit=100):
            if msg.author == self.bot.user and msg.id not in valid:
                try:
                    await msg.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass

    async def refresh(self):
        channel = self.bot.get_channel(CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            return

        guild = channel.guild
        altnames = self.load_altnames()
        content1, content2 = self.build_contents(guild, altnames)

        for i, content in enumerate([content1, content2]):
            msg = await self.fetch_or_none(channel, self._message_ids[i])
            if msg is None:
                msg = await channel.send(content)
                self._message_ids[i] = msg.id
                self._save_state()
            elif msg.content != content:
                await msg.edit(content=content)

        await self.cleanup_channel(channel)
