import subprocess
import shlex
from redbot.core import commands


class ExecVE(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.is_owner()
    @commands.command()
    async def execve(self, ctx, *, cmexec: str):
        args = shlex.split(cmexec)
        try:
            proc = subprocess.run(
                args, shell=True, capture_output=True, text=True, timeout=1
            )
            ret = proc.returncode
            if proc.returncode != 0:
                await ctx.send(f"failed execve, ret = {ret}")
                return

            out = proc.stdout
            MAX_LEN = 3000  # leave space for ``` code block
            for i in range(0, len(out), MAX_LEN):
                await ctx.send(f"```\n{out[i : i + MAX_LEN]}\n```")
        except Exception:
            await ctx.send(f"somehting died: {Exception}")
