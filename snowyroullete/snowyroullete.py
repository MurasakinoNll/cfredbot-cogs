import random
import discord
from redbot.core import Config, commands

UID = 512631443625869332


class SnowyRoullete(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.Enabled = False

    @commands.admin()
    @commands.command()
    async def fucksnowy(self, ctx):
        self.enabled = not self.enabled
        state = "ENABLED" if self.enabled else "DISABLED"
        await ctx.send(state)

        @commands.Cog.listener()
        async def on_message(self, member: discord.Member):
            snowy = member
            if not self.enabled:
                return
            if member.guild is None:
                return
            if snowy != UID:
                return
            if random.random() < 0.002:
                try:
                    await snowy.ban(reason="lowrolled")
                    await ctx.send("snowy got fucked")
                except discord.Forbidden:
                    pass
