import random
import discord
from redbot.core import Config, commands

UID = 512631443625869332


class SnowyRoullete(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=987654321)
        self.config.register_guild(enabled=False)
        self.config.register_user(chance=0.2)
        self.increment = 0.5

    @commands.is_owner()
    @commands.admin()
    async def sr(self, ctx):
        """Snowy Roulette controls."""
        enabled = await self.config.guild(ctx.guild).enabled()
        await ctx.send(
            f"SnowyRoulette is currently {'ENABLED' if enabled else 'DISABLED'}."
        )

    async def enable(self, ctx):
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("SnowyRoulette enabled.")

    async def disable(self, ctx):
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("SnowyRoulette disabled.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return

        if message.author.id != UID or 821998767652995083:
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
