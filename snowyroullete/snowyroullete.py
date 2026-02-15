import random
import discord
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
        if not self.enabled:
            return

        if message.guild is None:
            return

        if message.author.id != dbgUID:
            await message.channel.send(f"dbg ret {message.author.id} != {dbgUID}")
            return

        if self.current_chance is None:
            self.current_chance = self.base_chance

        roll = random.uniform(0, 100)
        threshold = self.current_chance

        if roll < threshold:
            try:
                await message.guild.ban(message.author, reason="lowrolled")
                await message.channel.send(
                    f"Roll: {roll:.3f}\nThreshold: {threshold:.3f}%\nResult: BANNED."
                )
            except discord.Forbidden:
                await message.channel.send(
                    f"Roll: {roll:.3f}\n"
                    f"Threshold: {threshold:.3f}%\n"
                    f"Ban failed (missing permissions)."
                )

            self.current_chance = self.base_chance

        else:
            self.current_chance += self.increment

            await message.channel.send(
                f"Roll: {roll:.3f}\n"
                f"Threshold: {threshold:.3f}%\n"
                f"Result: Survived.\n"
                f"New odds: {self.current_chance:.3f}%"
            )
