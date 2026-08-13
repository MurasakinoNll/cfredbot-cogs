import asyncio
import os
import pty
import re
import threading
import subprocess
import signal

from redbot.core import commands

TERMINAL_CHANNEL_ID = 1486807756290785400
USER_TERMINAL_CHANNEL_ID = 1537485660607483945

NSJAIL_BIN = "/usr/bin/nsjail"
USER_JAIL_ROOT = "/srv/nsjail_root"
USER_JAIL_WORKDIR = "/srv/nsjail_home"
JAIL_RLIMIT_AS_MB = 512
JAIL_RLIMIT_CPU_SECONDS = 3600
JAIL_RLIMIT_FSIZE_MB = 64
JAIL_RLIMIT_NOFILE = 64
ALLOW_NON_OWNER_IN_USER_TERMINAL = False

_DISCARD_RE = re.compile(
    r"\x1b\[[0-9;?]*[A-LN-Za-ln-z]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[()][AB012]"
    r"|\x1b[=>]"
    r"|\x1b[MDE78]"
    r"|\x9b[^@-~]*[@-~]"
    r"|\r",
    re.ASCII,
)

_SUPPORTED_FMT = {0, 1, 4}
_SUPPORTED_FG = set(range(30, 38))
_SUPPORTED_BG = set(range(40, 48))
_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
_ADJ_SGR_RE = re.compile(r"(\x1b\[[0-9;]*m)(\x1b\[[0-9;]*m)")


def _remap_sgr(match: re.Match) -> str:
    inner = match.group(1)
    raw_codes = inner.split(";") if inner else ["0"]
    out = []
    codes = list(raw_codes)
    i = 0
    while i < len(codes):
        part = codes[i]
        i += 1
        try:
            n = int(part)
        except ValueError:
            continue
        if n in (38, 48):
            if i < len(codes):
                mode = codes[i]
                i += 1
                if mode == "5" and i < len(codes):
                    i += 1
                elif mode == "2" and i + 2 < len(codes):
                    i += 3
            out.append("0")
            continue
        if n in _SUPPORTED_FMT or n in _SUPPORTED_FG or n in _SUPPORTED_BG:
            out.append(str(n))
        elif 90 <= n <= 97:
            out.append(str(n - 60))
        elif 100 <= n <= 107:
            out.append(str(n - 60))
        else:
            out.append("0")
    if not out:
        return ""
    deduped: list[str] = []
    for c in out:
        if c != "0" or not deduped or deduped[-1] != "0":
            deduped.append(c)
    return f"\x1b[{';'.join(deduped)}m"


def _consolidate_sgr(text: str) -> str:
    def merge(m: re.Match) -> str:
        a_inner = m.group(1)[2:-1]
        b_inner = m.group(2)[2:-1]
        b_codes = b_inner.split(";") if b_inner else ["0"]
        if b_codes[0] in ("", "0"):
            return m.group(2)
        seen = set()
        merged = []
        for c in filter(None, (a_inner + ";" + b_inner).split(";")):
            if c not in seen:
                seen.add(c)
                merged.append(c)
        return f"\x1b[{';'.join(merged)}m"

    prev = None
    while prev != text:
        prev = text
        text = _ADJ_SGR_RE.sub(merge, text)
    return text


def normalize_ansi(raw: str) -> str:
    text = _DISCARD_RE.sub("", raw)
    text = _SGR_RE.sub(_remap_sgr, text)
    text = _consolidate_sgr(text)
    text = re.sub(r"\x1b(?!\[)", "", text)
    return text


def chunk_text(text: str, size: int = 1900):
    for i in range(0, len(text), size):
        yield text[i : i + size]


def _build_command(jailed: bool) -> list[str]:
    if not jailed:
        return ["/bin/bash", "--norc", "--noprofile"]
    return [
        NSJAIL_BIN,
        "--mode",
        "o",
        "--user",
        "99999",
        "--group",
        "99999",
        "--hostname",
        "sandbox",
        "--cwd",
        "/root",
        "--chroot",
        USER_JAIL_ROOT,
        "--bindmount_ro",
        "/bin",
        "--bindmount_ro",
        "/usr",
        "--bindmount_ro",
        "/lib",
        "--bindmount_ro",
        "/etc/resolv.conf",
        "--bindmount",
        f"{USER_JAIL_WORKDIR}:/root",
        "--rlimit_as",
        str(JAIL_RLIMIT_AS_MB),
        "--rlimit_cpu",
        str(JAIL_RLIMIT_CPU_SECONDS),
        "--rlimit_fsize",
        str(JAIL_RLIMIT_FSIZE_MB),
        "--rlimit_nofile",
        str(JAIL_RLIMIT_NOFILE),
        "--time_limit",
        "0",
        "--env",
        "TERM=xterm-256color",
        "--env",
        "PS1=$ ",
        "--env",
        "HOME=/root",
        "--",
        "/bin/bash",
        "--norc",
        "--noprofile",
    ]


class _Session:
    __slots__ = ("proc", "master_fd", "running", "thread", "jailed")

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.master_fd: int | None = None
        self.running = False
        self.thread: threading.Thread | None = None
        self.jailed = False


class ExecPty(commands.Cog):
    """
    Persistent PTY terminal session(s) bridged to Discord channels.
    TERMINAL_CHANNEL_ID runs unrestricted. USER_TERMINAL_CHANNEL_ID runs under nsjail.
    """

    def __init__(self, bot):
        self.bot = bot
        self._sessions: dict[int, _Session] = {}

    def _is_jailed_channel(self, channel_id: int) -> bool:
        return channel_id == USER_TERMINAL_CHANNEL_ID

    def _valid_channel(self, channel_id: int) -> bool:
        return channel_id in (TERMINAL_CHANNEL_ID, USER_TERMINAL_CHANNEL_ID)

    def _kill_session(self, channel_id: int):
        session = self._sessions.get(channel_id)
        if not session:
            return
        session.running = False
        if session.proc and session.proc.poll() is None:
            try:
                os.killpg(os.getpgid(session.proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            session.proc = None
        if session.master_fd is not None:
            try:
                os.close(session.master_fd)
            except OSError:
                pass
            session.master_fd = None

    def _start_session(self, channel_id: int):
        jailed = self._is_jailed_channel(channel_id)
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            _build_command(jailed),
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            preexec_fn=os.setsid,
            env={**os.environ, "TERM": "xterm-256color", "PS1": "$ "},
        )
        os.close(slave_fd)
        session = _Session()
        session.proc = proc
        session.master_fd = master_fd
        session.running = True
        session.jailed = jailed
        self._sessions[channel_id] = session
        return master_fd, proc, session

    def _send_to_pty(self, channel_id: int, text: str):
        session = self._sessions.get(channel_id)
        if not session or session.master_fd is None:
            raise RuntimeError("No active PTY session for this channel.")
        data = text.replace("\n", "\r")
        if not data.endswith("\r"):
            data += "\r"
        os.write(session.master_fd, data.encode())

    def _reader_loop(
        self, master_fd: int, loop: asyncio.AbstractEventLoop, channel_id: int
    ):
        import select

        FLUSH_TIMEOUT = 0.35
        MAX_BLOCK = 1900
        raw_buf = b""
        text_buf = ""

        def flush():
            nonlocal text_buf
            if not text_buf.strip():
                text_buf = ""
                return
            block = text_buf
            text_buf = ""
            while block:
                if len(block) <= MAX_BLOCK:
                    asyncio.run_coroutine_threadsafe(
                        self._send_output(channel_id, block.strip()), loop
                    )
                    break
                split_at = block.rfind("\n", 0, MAX_BLOCK)
                if split_at == -1:
                    split_at = MAX_BLOCK
                part, block = block[:split_at], block[split_at:]
                asyncio.run_coroutine_threadsafe(
                    self._send_output(channel_id, part.strip()), loop
                )

        session = self._sessions.get(channel_id)

        while session and session.running:
            ready, _, _ = select.select([master_fd], [], [], FLUSH_TIMEOUT)
            if not ready:
                if raw_buf:
                    text_buf += normalize_ansi(raw_buf.decode(errors="replace"))
                    raw_buf = b""
                flush()
                continue
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            raw_buf += chunk
            while b"\n" in raw_buf:
                line_bytes, raw_buf = raw_buf.split(b"\n", 1)
                line = line_bytes.decode(errors="replace") + "\n"
                text_buf += normalize_ansi(line)
                if len(text_buf) >= MAX_BLOCK:
                    if raw_buf:
                        text_buf += normalize_ansi(raw_buf.decode(errors="replace"))
                        raw_buf = b""
                    flush()

        if raw_buf:
            text_buf += normalize_ansi(raw_buf.decode(errors="replace"))
        flush()

        if session and session.running:
            asyncio.run_coroutine_threadsafe(
                self._send_output(channel_id, "PTY process exited."), loop
            )
        if session:
            session.running = False
            session.proc = None
            session.master_fd = None

    async def _send_output(self, channel_id: int, text: str):
        channel = self.bot.get_channel(channel_id)
        if channel:
            try:
                await channel.send(f"```ansi\n{text}\n```")
            except Exception:
                pass

    @commands.is_owner()
    @commands.command()
    async def ptystart(self, ctx):
        """Start a PTY session bound to the channel this command is run in."""
        channel_id = ctx.channel.id
        if not self._valid_channel(channel_id):
            await ctx.send(
                "Run this in the terminal channel or the user terminal channel."
            )
            return

        existing = self._sessions.get(channel_id)
        if existing and existing.running:
            await ctx.send("A PTY session is already running in this channel.")
            return

        try:
            master_fd, proc, session = self._start_session(channel_id)
        except Exception as e:
            await ctx.send(f"Failed to start PTY: {type(e).__name__}: {e}")
            return

        loop = asyncio.get_event_loop()
        session.thread = threading.Thread(
            target=self._reader_loop,
            args=(master_fd, loop, channel_id),
            daemon=True,
        )
        session.thread.start()

        mode = "jailed (nsjail)" if session.jailed else "unrestricted"
        await ctx.send(
            f"PTY session started (PID `{proc.pid}`, {mode}) in <#{channel_id}>."
        )

    @commands.is_owner()
    @commands.command()
    async def ptystop(self, ctx):
        """Kill the PTY session bound to this channel."""
        channel_id = ctx.channel.id
        session = self._sessions.get(channel_id)
        if not session or not session.running:
            await ctx.send("No active PTY session in this channel.")
            return
        self._kill_session(channel_id)
        await ctx.send("PTY session terminated.")

    @commands.command()
    async def pty(self, ctx, *, cmd: str):
        """Send input to the PTY session bound to this channel."""
        channel_id = ctx.channel.id
        if not self._valid_channel(channel_id):
            return
        if channel_id == TERMINAL_CHANNEL_ID or not ALLOW_NON_OWNER_IN_USER_TERMINAL:
            if not await self.bot.is_owner(ctx.author):
                return

        session = self._sessions.get(channel_id)
        if not session or not session.running:
            await ctx.send("No active PTY session. Use `!ptystart` first.")
            return
        try:
            self._send_to_pty(channel_id, cmd)
        except Exception as e:
            await ctx.send(f"Failed to write to PTY: {e}")

    @commands.command()
    async def ptykey(self, ctx, *, key: str):
        """
        Send a special key or control sequence to the PTY bound to this channel.

        Named keys (case-insensitive):
          return / enter    Carriage return (\\r)
          tab               Horizontal tab (\\t)
          escape / esc      Escape key (\\x1b)
          backspace / bs    Backspace (\\x7f)
          delete / del      Delete (\\x1b[3~)
          up / down / right / left   Arrow keys
          home / end
          pageup / pgup / pagedown / pgdn
          f1 .. f12

        Control characters: ctrl+a .. ctrl+z
        Chain with spaces: !ptykey escape escape return
        Raw byte: !ptykey hex:1b
        """
        channel_id = ctx.channel.id
        if not self._valid_channel(channel_id):
            return
        if channel_id == TERMINAL_CHANNEL_ID or not ALLOW_NON_OWNER_IN_USER_TERMINAL:
            if not await self.bot.is_owner(ctx.author):
                return

        _NAMED = {
            "return": b"\r",
            "enter": b"\r",
            "tab": b"\t",
            "escape": b"\x1b",
            "esc": b"\x1b",
            "backspace": b"\x7f",
            "bs": b"\x7f",
            "delete": b"\x1b[3~",
            "del": b"\x1b[3~",
            "up": b"\x1b[A",
            "down": b"\x1b[B",
            "right": b"\x1b[C",
            "left": b"\x1b[D",
            "home": b"\x1b[H",
            "end": b"\x1b[F",
            "pageup": b"\x1b[5~",
            "pgup": b"\x1b[5~",
            "pagedown": b"\x1b[6~",
            "pgdn": b"\x1b[6~",
            "f1": b"\x1bOP",
            "f2": b"\x1bOQ",
            "f3": b"\x1bOR",
            "f4": b"\x1bOS",
            "f5": b"\x1b[15~",
            "f6": b"\x1b[17~",
            "f7": b"\x1b[18~",
            "f8": b"\x1b[19~",
            "f9": b"\x1b[20~",
            "f10": b"\x1b[21~",
            "f11": b"\x1b[23~",
            "f12": b"\x1b[24~",
        }

        session = self._sessions.get(channel_id)
        if not session or not session.running:
            await ctx.send("No active PTY session. Use `!ptystart` first.")
            return
        if session.master_fd is None:
            await ctx.send("No active PTY fd.")
            return

        payload = b""
        tokens = key.lower().split()
        unknown = []
        for token in tokens:
            if token.startswith("hex:"):
                try:
                    payload += bytes.fromhex(token[4:])
                    continue
                except ValueError:
                    unknown.append(token)
                    continue
            if token.startswith("ctrl+") and len(token) == 6:
                ch = token[5]
                if "a" <= ch <= "z":
                    payload += bytes([ord(ch) - ord("a") + 1])
                    continue
            if token in _NAMED:
                payload += _NAMED[token]
                continue
            unknown.append(token)

        if unknown:
            await ctx.send(
                f"Unknown key(s): {', '.join(f'`{u}`' for u in unknown)}. "
                f"See `!help ptykey` for the full list."
            )
            return
        if not payload:
            await ctx.send("No keys to send.")
            return
        try:
            os.write(session.master_fd, payload)
        except Exception as e:
            await ctx.send(f"PTY write error: {e}")

    @commands.is_owner()
    @commands.command()
    async def ptyrefresh(self, ctx):
        """Force the PTY program bound to this channel to redraw its screen."""
        channel_id = ctx.channel.id
        session = self._sessions.get(channel_id)
        if not session or not session.running or session.proc is None:
            await ctx.send("No active PTY session in this channel.")
            return
        if session.master_fd is None:
            await ctx.send("No active PTY fd.")
            return
        try:
            import fcntl
            import termios
            import struct

            COLS, ROWS = 220, 50
            winsize = struct.pack("HHHH", ROWS, COLS, 0, 0)
            fcntl.ioctl(session.master_fd, termios.TIOCSWINSZ, winsize)
            os.killpg(os.getpgid(session.proc.pid), signal.SIGWINCH)
            await ctx.send("Sent SIGWINCH, screen should redraw momentarily.")
        except Exception as e:
            await ctx.send(f"Refresh failed: {type(e).__name__}: {e}")

    @commands.is_owner()
    @commands.command()
    async def ptystatus(self, ctx):
        """Show PTY session status for every tracked channel."""
        if not self._sessions:
            await ctx.send("No active PTY sessions.")
            return
        lines = []
        for channel_id, session in self._sessions.items():
            if not session.running or session.proc is None:
                lines.append(f"<#{channel_id}>: no active session")
                continue
            rc = session.proc.poll()
            mode = "jailed" if session.jailed else "unrestricted"
            if rc is None:
                lines.append(
                    f"<#{channel_id}>: running, PID `{session.proc.pid}`, {mode}"
                )
            else:
                lines.append(f"<#{channel_id}>: exited, code `{rc}`")
        await ctx.send("\n".join(lines))

    @commands.Cog.listener()
    async def on_message(self, message):
        """
        Forward messages sent in either terminal channel directly to that
        channel's PTY, skipping ones that resolve to a registered bot command.
        """
        channel_id = message.channel.id
        if not self._valid_channel(channel_id):
            return
        if message.author.bot:
            return

        if channel_id == TERMINAL_CHANNEL_ID or not ALLOW_NON_OWNER_IN_USER_TERMINAL:
            if not await self.bot.is_owner(message.author):
                return

        content = message.content.strip()
        if not content:
            return

        ctx = await self.bot.get_context(message)
        if ctx.valid and ctx.command is not None:
            return

        session = self._sessions.get(channel_id)
        if not session or not session.running:
            await message.channel.send(
                "No active PTY session. Use `!ptystart` to begin.", delete_after=5
            )
            return

        try:
            self._send_to_pty(channel_id, content)
        except Exception as e:
            await message.channel.send(f"PTY write error: {e}")
