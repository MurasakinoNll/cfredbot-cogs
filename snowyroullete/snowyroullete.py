import random
import discord
import time
from redbot.core import Config, commands

UID = 512631443625869332
dbgUID = 1049116028552093816


class SnowyRoullete(commands.Cog):
    def __init__(self, bot):
        self.config = Config.get_conf(self, identifier=987654321)

        self.bot = bot
        self.enabled = False
        self.base_chance = 0.2  # %
        self.increment = 0.5
        self.current_chance = self.base_chance

    @commands.admin()
    @commands.command()
    async def sr(self, ctx, state: str):
        state = state.lower()

        if state == "enable":
            self.enabled = True
            await ctx.send("SnowyRoulette enabled.")
        elif state == "disable":
            self.enabled = False
            await ctx.send("SnowyRoulette disabled.")
        else:
            await ctx.send("Invalid option. Use: enable or disable.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return
        if message.author.bot:
            return
        if message.author.id not in (UID, dbgUID):
            return
        if not self.enabled:
            return

        roll = random.uniform(0, 100)
        threshold = self.current_chance

        if roll < threshold:
            try:
                await message.channel.send(
                    f"rolled a {roll:.1f} while threshold = {threshold:.1f}, bye snowy!"
                )
                time.sleep(1)
                await message.guild.kick(message.author, reason="lowrolled")
            except discord.Forbidden:
                await message.channel.send("error check log retard")
            self.current_chance = self.base_chance

        else:
            self.current_chance += self.increment

            await message.channel.send(
                f"rolled a {roll:.1f}, threshold = {threshold:1.f}"
            )
