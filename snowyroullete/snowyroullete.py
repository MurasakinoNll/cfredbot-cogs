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
        if message.guild is None:
            return

        if message.author.id != dbgUID:
            return

        current_chance = await self.config.user(message.author).chance()

        roll = random.uniform(0, 100)

        if roll < current_chance:
            await self.config.user(message.author).chance.set(0.2)

            try:
                await message.guild.ban(message.author, reason="lowrolled")
                await message.channel.send(
                    f"Roll: {roll:.3f}\n"
                    f"Threshold: {current_chance:.3f}%\n"
                    f"snowy got fucked."
                )
            except discord.Forbidden:
                await message.channel.send(
                    f"Roll: {roll:.3f}\nThreshold: {current_chance:.3f}%\nmissing perms"
                )
        else:
            new_chance = current_chance + self.increment
            await self.config.user(message.author).chance.set(new_chance)

            await message.channel.send(
                f"Roll: {roll:.3f}\n"
                f"Threshold: {current_chance:.3f}%\n"
                f"snowy survived.\n"
                f"New odds: {new_chance:.3f}%"
            )
