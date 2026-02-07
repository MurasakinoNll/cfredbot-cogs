import subprocess
from redbot.core import commands


class ExecVE(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.is_owner()
    @commands.command()
    async def execve(self, ctx, cmexec: str):
        try:
            proc = subprocess.run([cmexec], capture_output=True, text=True, timeout=1)
            ret = proc.returncode
            if proc.returncode != 0:
                await ctx.send(f"failed execve, ret = {ret}")
                return

            out = proc.stdout
            await ctx.send(out)

        except Exception:
            await ctx.send(f"somehting died: {Exception}")
