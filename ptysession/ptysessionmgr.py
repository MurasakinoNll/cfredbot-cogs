import asyncio
import os
import pty
import re
import threading
import subprocess
import signal

from redbot.core import commands

TERMINAL_CHANNEL_ID = 1486807756290785400

# ---------------------------------------------------------------------------
# ANSI processing
#
# Discord's ```ansi``` blocks support ONLY:
#   Formats : 0 (reset), 1 (bold), 4 (underline)
#   FG color: 30-37
#   BG color: 40-47
#
# Everything else must be stripped or remapped before sending.
# ---------------------------------------------------------------------------

# Sequences to discard entirely (cursor movement, erase, scroll, OSC,
# private modes, charset switching, application keypad, etc.)
_DISCARD_RE = re.compile(
    r"\x1b\[[0-9;?]*[ABCDEFGHJKLMPSTfhnsu]"  # cursor / erase / scroll (non-SGR)
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC sequences  e.g. ]10;? ]11;?
    r"|\x1b[()][AB012]"  # charset G0/G1 designation
    r"|\x1b[=>]"  # application / normal keypad
    r"|\x1b[MDE78]"  # reverse index, next line, etc.
    r"|\x9b[^@-~]*[@-~]"  # C1 CSI
    r"|\r",  # bare CR from PTY line discipline
    re.ASCII,
)

_SUPPORTED_FMT = {0, 1, 4}
_SUPPORTED_FG = set(range(30, 38))
_SUPPORTED_BG = set(range(40, 48))

# Matches any SGR sequence: ESC [ <codes> m
_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")


def _remap_sgr(match: re.Match) -> str:
    """
    Remap an SGR sequence to only Discord-supported codes.
    - High-intensity fg 90-97  → standard fg 30-37
    - High-intensity bg 100-107 → standard bg 40-47
    - Unsupported format codes  → reset (0)
    - 256-color / RGB           → dropped
    Returns '' if the result is empty.
    """
    inner = match.group(1)
    raw_codes = inner.split(";") if inner else ["0"]

    out = []
    skip = 0
    codes = list(raw_codes)
    i = 0
    while i < len(codes):
        part = codes[i]
        i += 1
        try:
            n = int(part)
        except ValueError:
            continue

        # 256-color / RGB: ESC[38;5;Nm, ESC[38;2;R;G;Bm, same for 48
        if n in (38, 48):
            if i < len(codes):
                mode = codes[i]
                i += 1
                if mode == "5" and i < len(codes):
                    i += 1  # skip palette index
                elif mode == "2" and i + 2 < len(codes):
                    i += 3  # skip R G B
            out.append("0")  # replace with reset
            continue

        if n in _SUPPORTED_FMT or n in _SUPPORTED_FG or n in _SUPPORTED_BG:
            out.append(str(n))
        elif 90 <= n <= 97:
            out.append(str(n - 60))  # bright fg → normal fg
        elif 100 <= n <= 107:
            out.append(str(n - 60))  # bright bg → normal bg
        else:
            out.append("0")  # anything else → reset

    if not out:
        return ""

    # Collapse consecutive resets into one
    deduped: list[str] = []
    for c in out:
        if c != "0" or not deduped or deduped[-1] != "0":
            deduped.append(c)

    return f"\x1b[{';'.join(deduped)}m"


def normalize_ansi(raw: str) -> str:
    """
    1. Strip all non-SGR escape sequences (cursor moves, OSC, etc.)
    2. Remap SGR codes to the Discord-supported subset.
    3. Kill any leftover bare ESC bytes.
    """
    text = _DISCARD_RE.sub("", raw)
    text = _SGR_RE.sub(_remap_sgr, text)
    text = text.replace("\x1b", "")
    return text


def chunk_text(text: str, size: int = 1900):
    for i in range(0, len(text), size):
        yield text[i : i + size]


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class ExecPty(commands.Cog):
    """
    Persistent PTY terminal session bridged to a Discord channel.
    All messages sent in the terminal channel are forwarded directly to bash.
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
        """
        Write input to the PTY using \\r as the line terminator.

        Real terminals send \\r (carriage return) when Enter is pressed, not \\n.
        Curses programs (vim, nano, htop, etc.) rely on this — sending \\n will
        cause the keypress to be ignored or misinterpreted.
        """
        if self._master_fd is None:
            raise RuntimeError("No active PTY session.")
        data = text.replace("\n", "\r")
        if not data.endswith("\r"):
            data += "\r"
        os.write(self._master_fd, data.encode())

    # ------------------------------------------------------------------ #
    #  Background reader                                                   #
    # ------------------------------------------------------------------ #

    def _reader_loop(
        self, master_fd: int, loop: asyncio.AbstractEventLoop, channel_id: int
    ):
        """
        Read PTY output in a background thread and forward to Discord.
        Buffers by newline; ANSI is normalized to Discord's supported subset.
        """
        buf = b""
        while self._running:
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                break

            if not chunk:
                break

            buf += chunk

            while b"\n" in buf or len(buf) > 1900:
                if b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line + b"\n"
                else:
                    line, buf = buf[:1900], buf[1900:]

                text = line.decode(errors="replace")
                text = normalize_ansi(text).strip()
                if not text:
                    continue

                for part in chunk_text(text):
                    asyncio.run_coroutine_threadsafe(
                        self._send_output(channel_id, part), loop
                    )

        # Drain leftover buffer
        if buf:
            text = normalize_ansi(buf.decode(errors="replace")).strip()
            if text:
                for part in chunk_text(text):
                    asyncio.run_coroutine_threadsafe(
                        self._send_output(channel_id, part), loop
                    )

        if self._running:
            asyncio.run_coroutine_threadsafe(
                self._send_output(channel_id, "PTY process exited."), loop
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

        await ctx.send(f"PTY session started (PID `{proc.pid}`). ")

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
        """Send input directly to the PTY session (works from any channel)."""
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
        Only messages that invoke an actual registered bot command are skipped
        to avoid double-sending.
        """
        if message.channel.id != TERMINAL_CHANNEL_ID:
            return
        if message.author.bot:
            return

        if not await self.bot.is_owner(message.author):
            return

        content = message.content.strip()
        if not content:
            return

        # Skip real registered bot commands to avoid double-sending
        ctx = await self.bot.get_context(message)
        if ctx.valid and ctx.command is not None:
            return

        if not self._running:
            await message.channel.send("No active PTY session.", delete_after=5)
            return

        try:
            self._send_to_pty(content)
        except Exception as e:
            await message.channel.send(f"PTY write error: {e}")
