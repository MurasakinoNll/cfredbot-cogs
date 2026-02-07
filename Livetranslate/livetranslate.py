import aiohttp
import discord
from redbot.core import commands, Config
import re

GOOGLE_TRANSLATE_URL = "https://translation.googleapis.com/language/translate/v2"


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

    GOOGLE_TRANSLATE_URL = "https://translation.googleapis.com/language/translate/v2"

    async def _translate(self, text: str) -> str | None:
        key = await self.config.api_key()
        if not key:
            return None

        params = {"key": key}
        payload = {
            "q": text,
            "target": "en",
            "format": "text",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    GOOGLE_TRANSLATE_URL,
                    params=params,
                    json=payload,
                    timeout=5,
                ) as resp:
                    if resp.status != 200:
                        return None

                    data = await resp.json()

            translations = data.get("data", {}).get("translations")
            if not translations:
                return None

            return translations[0].get("translatedText")
        except Exception:
            return None

    @commands.is_owner()
    @commands.command()
    async def settranslatekey(self, ctx, key: str):
        await self.config.api_key.set(key)
        await ctx.send("api key set")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.channel.id not in self.enabled_channels:
            return

        if message.content.startswith("!translate"):
            return
        if self._englishy(message.content):
            return
        translated = await self._translate(message.content)
        if translated is None:
            await message.channel.send(f"{message.author.name}: no translation")
        output = translated if translated else message.content

        await message.channel.send(f"{message.author.name}: {output}")
