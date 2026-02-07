import subprocess
from redbot.core import commands


def chunk_text(text: str, size: int = 1900):
    for i in range(0, len(text), size):
        yield text[i : i + size]


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

            out = proc.stdout or proc.stderr
            if not out:
                await ctx.send("no output")

            for chunk in chunk_text(out):
                await ctx.send(f"```{chunk}```")

        except Exception as e:
            await ctx.send(f"somehting died: {e}")
