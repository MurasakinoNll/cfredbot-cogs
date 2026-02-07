import aiohttp
import discord
from redbot.core import commands, Config
import re

YANDEX_DICT_URL = "https://dictionary.yandex.net/api/v1/dicservice.json/lookup"


class LiveTranslate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=123456789)
        self.config.register_global(api_key=None)
        self.enabled_channels = set()

    @commands.command()
    async def translatetext(self, ctx):
        cid = ctx.channel.id

        if cid in self.enabled_channels:
            self.enabled_channels.remove(cid)
            await ctx.send("disabled translate")
        else:
            self.enabled_channels.add(cid)
            await ctx.send("enabled translate")

    def _englishy(self, text: str) -> bool:
        ascii_ratio = sum(c.isascii() for c in text) / max(len(text), 1)
        return ascii_ratio > 0.9

    def _lang_check(self, text: str) -> str:
        if re.search(r"[а-яА-Я]", text):
            return "ru-en"
        if re.search(r"[ぁ-んァ-ン一-龯]", text):
            return "ja-en"
        if re.search(r"[가-힣]", text):
            return "ko-en"
        return "it-en"

    async def _translate(self, text: str) -> str | None:
        key = await self.config.api_key()
        params = {
            "key": key,
            "lang": self._lang_check(text),
            "text": text,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    YANDEX_DICT_URL, params=params, timeout=5
                ) as resp:
                    if resp.status != 200:
                        return None

                    data = await resp.json()
                defs = data.get("def")
                if not defs:
                    return None
                trs = defs[0].get("tr")
                if not trs:
                    return None

                return trs[0].get("text")
        except Exception:
            return None

    @commands.is_owner()
    @commands.command()
    async def settranslatekey(self, ctx, key: str):
        await self.config.api_key.set(key)
        await ctx.send("api key set")

    @commands.Cog.listener()
    async def on_msg(self, message: discord.Message):
        if message.author.bot:
            return
        if message.channel.id not in self.enabled_channels:
            return

        if message.content.startswith("!translate"):
            return
        if self._englishy(message.content):
            return
        translated = await self._translate(message.content)
        output = translated if translated else message.content

        await message.channel.send(f"{message.author.name}: {output}")
