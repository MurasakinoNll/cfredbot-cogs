import asyncio
import os
import pty
import signal

from redbot.core import commands


class ExecPTY(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.sessions = {}  # channel_id -> (pid, master_fd)

    @commands.is_owner()
    @commands.command()
    async def execpty(self, ctx):
        cid = ctx.channel.id

        if cid in self.sessions:
            await ctx.send("session already running in this channel")
            return

        pid, master_fd = pty.fork()
        if pid == 0:
            # Child process: real shell
            os.execvp("/bin/bash", ["/bin/bash", "--noprofile", "--norc"])
            os._exit(1)

        # Parent
        self.sessions[cid] = (pid, master_fd)
        await ctx.send("pty shell started")

        asyncio.create_task(self._read_loop(ctx, cid, master_fd))

    async def _read_loop(self, ctx, cid, master_fd):
        loop = asyncio.get_running_loop()

        try:
            while cid in self.sessions:
                data = await loop.run_in_executor(None, os.read, master_fd, 1024)
                if not data:
                    break

                text = data.decode(errors="ignore")
                if text.strip():
                    await ctx.send(f"```{text[:1900]}```")
        except Exception:
            pass
        finally:
            self._cleanup(cid)

    @commands.is_owner()
    @commands.command()
    async def execptysend(self, ctx, *, data: str):
        cid = ctx.channel.id
        session = self.sessions.get(cid)

        if not session:
            await ctx.send("no active pty session")
            return

        _, master_fd = session
        os.write(master_fd, (data + "\n").encode())

    @commands.is_owner()
    @commands.command()
    async def execptystop(self, ctx):
        cid = ctx.channel.id
        session = self.sessions.get(cid)

        if not session:
            await ctx.send("no active session")
            return

        pid, master_fd = session
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass

        self._cleanup(cid)
        await ctx.send("pty session stopped")

    def _cleanup(self, cid):
        session = self.sessions.pop(cid, None)
        if not session:
            return

        _, master_fd = session
        try:
            os.close(master_fd)
        except Exception:
            pass
