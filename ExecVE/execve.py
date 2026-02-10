import subprocess
import asyncio
import re
from redbot.core import commands


def chunk_text(text: str, size: int = 1900):
    for i in range(0, len(text), size):
        yield text[i : i + size]


class ExecVE(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.sessions = {}

    @commands.is_owner()
    @commands.command()
    async def execpipekill(self, ctx):
        proc = self.sessions.get(ctx.channel.id)
        if not proc:
            await ctx.send("no active execpipe")
            return

        proc.terminate()
        await proc.wait()

        del self.sessions[ctx.channel.id]
        await ctx.send("execpipe terminated")

    @commands.is_owner()
    @commands.command()
    async def execpipe(self, ctx, action: str, *, cmd: str):
        cid = ctx.channel.id

        if action == "start":
            if cid in self.sessions:
                await ctx.send("session already running in this channel")
                return

            if not cmd:
                await ctx.send("no command provided")
                return

            try:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
            except Exception as e:
                await ctx.send(f"spawn failed: {type(e).__name__}: {e}")
                return

            self.sessions[cid] = proc
            await ctx.send(f"started:\n```{cmd}```")
            self.bot.loop.create_task(self._read_output(ctx, proc))

        elif action == "send":
            proc = self.sessions.get(cid)
            if not proc:
                await ctx.send("no active session")
                return

            if not cmd:
                await ctx.send("no input to send")
                return

            try:
                proc.stdin.write((cmd + "\n").encode())
                await proc.stdin.drain()
            except Exception as e:
                await ctx.send(f"stdin failed: {type(e).__name__}: {e}")

        elif action == "stop":
            proc = self.sessions.pop(cid, None)
            if not proc:
                await ctx.send("no active session")
                return

            proc.kill()
            await ctx.send("session terminated")

        else:
            await ctx.send("usage: execpipe <start|send|stop> ...")

    async def _read_output(self, ctx, proc):
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break

                text = line.decode(errors="ignore")
                for part in chunk_text(text):
                    await ctx.send(f"```{part}```")

            rc = await proc.wait()
            await ctx.send(f"process exited with code {rc}")

        except Exception as e:
            await ctx.send(f"read failed: {type(e).__name__}: {e}")
        finally:
            self.sessions.pop(ctx.channel.id, None)

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
                cleaned = re.sub(r"\x1b\[[^m]*[A-Za-z]", "", chunk)
                await ctx.send(f"```ansi\n{cleaned}```")

        except Exception as e:
            await ctx.send(f"somehting died: {e}")
