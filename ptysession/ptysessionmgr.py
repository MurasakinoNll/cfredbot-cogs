import asyncio
import os
import pty
import re
import threading
import subprocess
import signal

from redbot.core import commands

TERMINAL_CHANNEL_ID = 1486807756290785400
ANSI_ESCAPE = re.compile(r"(?:\x9B|\x1B\[)[0-9;?]*[A-Za-ln-z]|\x1B[()][AB012]|\x1B=|\r")


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub("", text)


def chunk_text(text: str, size: int = 1900):
    for i in range(0, len(text), size):
        yield text[i : i + size]


class ExecPty(commands.Cog):
    """
    Persistent PTY terminal session bridged to a Discord channel.
    All messages sent in the terminal channel are forwarded directly to the shell.
    """

    def __init__(self, bot):
        self.bot = bot
        self._proc: subprocess.Popen | None = None
        self._master_fd: int | None = None
        self._reader_thread: threading.Thread | None = None
        self._running = False

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _kill_session(self):
        self._running = False

        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            self._proc = None

        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None

    def _start_session(self):
        """Spawn a persistent bash shell inside a PTY."""
        master_fd, slave_fd = pty.openpty()

        proc = subprocess.Popen(
            ["/bin/bash", "--norc", "--noprofile"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            preexec_fn=os.setsid,
            env={
                **os.environ,
                "TERM": "xterm-256color",
                "PS1": "$ ",
            },
        )

        os.close(slave_fd)

        self._proc = proc
        self._master_fd = master_fd
        self._running = True

        return master_fd, proc

    def _send_to_pty(self, text: str):
        """Write a line of text to the PTY master fd."""
        if self._master_fd is None:
            raise RuntimeError("No active PTY session.")
        data = (text + "\n").encode()
        os.write(self._master_fd, data)

    # ------------------------------------------------------------------ #
    #  Background reader                                                   #
    # ------------------------------------------------------------------ #

    def _reader_loop(
        self, master_fd: int, loop: asyncio.AbstractEventLoop, channel_id: int
    ):
        """
        Read PTY output in a background thread and schedule Discord sends
        on the bot's event loop.
        """
        buf = b""
        while self._running:
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                # PTY closed
                break

            if not chunk:
                break

            buf += chunk

            # Flush on newlines so we don't hold partial lines forever,
            # but also flush if the buffer gets large.
            while b"\n" in buf or len(buf) > 1900:
                if b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line + b"\n"
                else:
                    line, buf = buf[:1900], buf[1900:]

                text = line.decode(errors="replace")
                text = strip_ansi(text).strip()
                if not text:
                    continue

                for part in chunk_text(text):
                    asyncio.run_coroutine_threadsafe(
                        self._send_output(channel_id, part), loop
                    )

        # Drain any leftover
        if buf:
            text = strip_ansi(buf.decode(errors="replace")).strip()
            if text:
                for part in chunk_text(text):
                    asyncio.run_coroutine_threadsafe(
                        self._send_output(channel_id, part), loop
                    )

        if self._running:
            asyncio.run_coroutine_threadsafe(
                self._send_output(channel_id, "⚠️ PTY process exited."), loop
            )
        self._running = False
        self._proc = None
        self._master_fd = None

    async def _send_output(self, channel_id: int, text: str):
        channel = self.bot.get_channel(channel_id)
        if channel:
            try:
                await channel.send(f"```ansi\n{text}\n```")
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    #  Commands                                                            #
    # ------------------------------------------------------------------ #

    @commands.is_owner()
    @commands.command()
    async def ptystart(self, ctx):
        """Start the persistent PTY bash session in the terminal channel."""
        if self._running:
            await ctx.send("A PTY session is already running.")
            return

        channel = self.bot.get_channel(TERMINAL_CHANNEL_ID)
        if channel is None:
            await ctx.send(
                f"Cannot find channel `{TERMINAL_CHANNEL_ID}`. Check the ID."
            )
            return

        try:
            master_fd, proc = self._start_session()
        except Exception as e:
            await ctx.send(f"Failed to start PTY: {type(e).__name__}: {e}")
            return

        loop = asyncio.get_event_loop()
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            args=(master_fd, loop, TERMINAL_CHANNEL_ID),
            daemon=True,
        )
        self._reader_thread.start()

        await ctx.send(
            f"✅ PTY session started (PID `{proc.pid}`). "
            f"Terminal channel: <#{TERMINAL_CHANNEL_ID}>. "
            f"Type anything there — it goes straight to bash."
        )

    @commands.is_owner()
    @commands.command()
    async def ptystop(self, ctx):
        """Kill the PTY session."""
        if not self._running:
            await ctx.send("No active PTY session.")
            return

        self._kill_session()
        await ctx.send("PTY session terminated.")

    @commands.is_owner()
    @commands.command()
    async def pty(self, ctx, *, cmd: str):
        """Send a command directly to the PTY session (works from any channel)."""
        if not self._running:
            await ctx.send("No active PTY session. Use `!ptystart` first.")
            return

        try:
            self._send_to_pty(cmd)
        except Exception as e:
            await ctx.send(f"Failed to write to PTY: {e}")

    @commands.is_owner()
    @commands.command()
    async def ptystatus(self, ctx):
        """Show PTY session status."""
        if not self._running or self._proc is None:
            await ctx.send("No active PTY session.")
            return

        pid = self._proc.pid
        rc = self._proc.poll()
        if rc is None:
            await ctx.send(
                f"PTY session running — PID `{pid}`, master fd `{self._master_fd}`."
            )
        else:
            await ctx.send(f"PTY process exited with code `{rc}`.")

    # ------------------------------------------------------------------ #
    #  Passthrough listener — terminal channel acts as a raw terminal     #
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_message(self, message):
        """
        Any message sent in the terminal channel by the bot owner is
        forwarded to the PTY as-is (no command prefix needed).
        Commands (!pty*, !exec*) are ignored here to avoid double-sending.
        """
        if message.channel.id != TERMINAL_CHANNEL_ID:
            return
        if message.author.bot:
            return

        # Only the bot owner may use the terminal channel as a raw PTY
        app_info = await self.bot.application_info()
        if message.author.id != app_info.owner.id:
            return

        content = message.content.strip()
        if not content:
            return

        # Skip lines that are already bot commands so they aren't double-sent
        prefixes = await self.bot.get_prefix(message)
        if isinstance(prefixes, str):
            prefixes = [prefixes]
        if any(content.startswith(p) for p in prefixes):
            return

        if not self._running:
            await message.channel.send(
                "⚠️ No active PTY session. Use `!ptystart` to begin.", delete_after=5
            )
            return

        try:
            self._send_to_pty(content)
        except Exception as e:
            await message.channel.send(f"PTY write error: {e}")
