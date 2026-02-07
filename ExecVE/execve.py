import subprocess
from redbot.core import commands


class ExecVE(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.is_owner()
    @commands.command()
    async def execve(self, ctx):
        content = ctx.message.content
        prefix = ctx.prefix + ctx.command.qualified_name
        if not content.startswith(prefix):
            return

        cmexec = content[len(prefix) :].lstrip()
        if not cmexec:
            await ctx.send("invalid input")
            return
        try:
            proc = subprocess.run(
                cmexec, shell=True, capture_output=True, text=True, timeout=1
            )
            ret = proc.returncode
            if proc.returncode != 0:
                await ctx.send(f"failed execve, ret = {ret}")
                return

            out = proc.stdout
            MAX_LEN = 3000
            for i in range(0, len(out), MAX_LEN):
                await ctx.send(f"```\n{out[i : i + MAX_LEN]}\n```")
        except Exception as e:
            await ctx.send(f"somehting died: {e}")
