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

# Sequences to discard entirely.
#
# Key fix: the final-byte set covers [A-LN-Za-ln-z] — every CSI final byte
# EXCEPT 'm' (SGR). This correctly catches ?25l (hide cursor), ?2004l
# (bracketed paste), ?7l (no-wrap), cursor moves, erase, scroll — all of them.
# The old pattern listed specific letters and was missing 'l', causing private
# mode sequences like ESC[?25l to survive as literal garbage in output.
_DISCARD_RE = re.compile(
    r"\x1b\[[0-9;?]*[A-LN-Za-ln-z]"  # all CSI with final byte != m
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC  e.g. ]10;? ]11;?
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

# Two adjacent SGR sequences that can be merged into one.
# ESC[0mESC[1m  →  ESC[0;1m  — reduces noise from apps that emit one code at a time.
_ADJ_SGR_RE = re.compile(r"(\x1b\[[0-9;]*m)(\x1b\[[0-9;]*m)")


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


def _consolidate_sgr(text: str) -> str:
    """
    Merge adjacent SGR sequences, respecting reset semantics.

    - ESC[37mESC[0;1m  -> ESC[0;1m   (37 is wiped by the following reset, drop it)
    - ESC[0mESC[1m     -> ESC[0;1m   (safe merge)
    - ESC[31mESC[1m    -> ESC[31;1m  (safe merge, no reset)

    Repeats until stable to handle runs of 3+.
    """

    def merge(m: re.Match) -> str:
        a_inner = m.group(1)[2:-1]
        b_inner = m.group(2)[2:-1]
        b_codes = b_inner.split(";") if b_inner else ["0"]
        # If the second sequence starts with a reset, the first is fully
        # overridden — discard it and keep only the second.
        if b_codes[0] in ("", "0"):
            return m.group(2)
        # Deduplicate codes while preserving order (last reset already handled above)
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
    """
    1. Strip all non-SGR escape sequences (cursor moves, OSC, private modes, etc.)
    2. Remap SGR codes to the Discord-supported subset.
    3. Merge adjacent SGR sequences to reduce noise.
    4. Kill any leftover bare ESC bytes.
    """
    text = _DISCARD_RE.sub("", raw)
    text = _SGR_RE.sub(_remap_sgr, text)
    text = _consolidate_sgr(text)
    # Only strip truly orphaned ESC bytes (not followed by '['), not valid sequences
    text = re.sub(r"\x1b(?!\[)", "", text)
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

        Lines are batched together and flushed as a single ```ansi``` block
        so that ANSI color state flows correctly across lines — a color set on
        line 1 remains active on line 2 because they share the same code block.

        Flush triggers:
          - Accumulated text would exceed Discord's 1900-char safe limit
          - No new data arrives within FLUSH_TIMEOUT seconds (output gone quiet)
        """
        import select

        FLUSH_TIMEOUT = 0.35  # seconds of silence before flushing
        MAX_BLOCK = 1900  # safe chars per ```ansi``` block

        raw_buf = b""  # unprocessed bytes from the PTY
        text_buf = ""  # normalized text waiting to be sent

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
                # Split cleanly on the last newline before the limit
                split_at = block.rfind("\n", 0, MAX_BLOCK)
                if split_at == -1:
                    split_at = MAX_BLOCK
                part, block = block[:split_at], block[split_at:]
                asyncio.run_coroutine_threadsafe(
                    self._send_output(channel_id, part.strip()), loop
                )

        while self._running:
            # Wait up to FLUSH_TIMEOUT for data; silence means flush
            ready, _, _ = select.select([master_fd], [], [], FLUSH_TIMEOUT)
            if not ready:
                flush()
                continue

            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                break

            if not chunk:
                break

            raw_buf += chunk

            # Drain all complete lines into text_buf
            while b"\n" in raw_buf:
                line_bytes, raw_buf = raw_buf.split(b"\n", 1)
                line = line_bytes.decode(errors="replace") + "\n"
                text_buf += normalize_ansi(line)

                # Pre-emptively flush if we're already near the limit
                if len(text_buf) >= MAX_BLOCK:
                    flush()

        # Drain remaining bytes then final flush
        if raw_buf:
            text_buf += normalize_ansi(raw_buf.decode(errors="replace"))
        flush()

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
    async def ptykey(self, ctx, *, key: str):
        """
        Send a special key or control sequence to the PTY.

        Named keys (case-insensitive):
          return / enter    Carriage return (\r)
          tab               Horizontal tab (\t)
          escape / esc      Escape key (\x1b)
          backspace / bs    Backspace (\x7f)
          delete / del      Delete (\x1b[3~)
          up                Arrow up (\x1b[A)
          down              Arrow down (\x1b[B)
          right             Arrow right (\x1b[C)
          left              Arrow left (\x1b[D)
          home              Home (\x1b[H)
          end               End (\x1b[F)
          pageup / pgup     Page up (\x1b[5~)
          pagedown / pgdn   Page down (\x1b[6~)
          f1 .. f12         Function keys

        Control characters: ctrl+a .. ctrl+z  (e.g. ctrl+c, ctrl+d, ctrl+z)

        You can also chain keys with spaces: !ptykey escape escape return
        Or send a raw hex byte: !ptykey hex:1b
        """
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

        if not self._running:
            await ctx.send("No active PTY session. Use `!ptystart` first.")
            return

        if self._master_fd is None:
            await ctx.send("No active PTY fd.")
            return

        payload = b""
        tokens = key.lower().split()
        unknown = []

        for token in tokens:
            # Raw hex: hex:1b  or  hex:0d
            if token.startswith("hex:"):
                try:
                    payload += bytes.fromhex(token[4:])
                    continue
                except ValueError:
                    unknown.append(token)
                    continue

            # ctrl+x  →  control character 0x01-0x1a
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
            os.write(self._master_fd, payload)
        except Exception as e:
            await ctx.send(f"PTY write error: {e}")

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
            await message.channel.send(
                "⚠️ No active PTY session. Use `!ptystart` to begin.", delete_after=5
            )
            return

        try:
            self._send_to_pty(content)
        except Exception as e:
            await message.channel.send(f"PTY write error: {e}")
