import subprocess
from redbot.core import commands


class MeasureTemp(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def temp(self, ctx):
        """measure system temp with vcgencmd"""
        try:
            proc = subprocess.run(
                ["vcgencmd", "measure_temp"], capture_output=True, text=True, timeout=1
            )
            ret = proc.returncode

            if proc.returncode != 0:
                await ctx.send(f"failed to read temp, ret = {ret}")
                return

            out = proc.stdout.strip()
            await ctx.send(out)

        except Exception:
            await ctx.send("something shit itself check log")
