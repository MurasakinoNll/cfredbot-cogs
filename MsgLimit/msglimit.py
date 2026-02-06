import discord
from redbot.core import commands, Config
from datetime import date


class MsgLimit(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=918273645)
        self.config.register_guild(users={})

    def _is_media_msg(self, message):
        if message.attachments:
            return True
        if message.embeds:
            return True
        if "http://" in message.content or "https://" in message.content:
            return True
        return False

    def _reset_24h(self, data):
        today = str(date.today())
        if data.get("date") != today:
            data["count"] = 0
            data["date"] = today

    @commands.guild_only()
    @commands.is_owner()
    @commands.command()
    async def msglimit(self, ctx, user: discord.Member, limit: int):
        """Set daily limit of text msgs"""
        if limit < 1:
            await ctx.send("limit must be an int > 1")
            return

        users = await self.config.guild(ctx.guild).users()
        uuid = str(user.id)

        users.setdefault(uuid, {})
        users[uuid]["text"] = {"limit": limit, "count": 0, "date": str(date.today())}

        await self.config.guild(ctx.guild).users.set(users)
        await ctx.send(f"text limit for `{user}` set to **`{limit}`**")

    @commands.guild_only()
    @commands.is_owner()
    @commands.command()
    async def medialimit(self, ctx, user: discord.Member, limit: int):
        """set daily limit of media msgs"""
        if limit < 1:
            await ctx.send("limit must be an int > 1")
            return

        users = await self.config.guild(ctx.guild).users()
        uuid = str(user.id)

        users.setdefault(uuid, {})
        users[uuid]["media"] = {"limit": limit, "count": 0, "date": str(date.today())}

        await self.config.guild(ctx.guild).users.set(users)
        await ctx.send(f"media limit for `{user}` set to **`{limit}`**")

    @commands.guild_only()
    @commands.is_owner()
    @commands.command()
    async def rmlimit(self, ctx, user: discord.Member):
        """remove message limits from user"""
        users = await self.config.guild(ctx.guild).users()
        uuid = str(user.id)

        if uuid not in users:
            await ctx.send("user has no msglimit set")
            return

        del users[uuid]
        await self.config.guild(ctx.guild).users.set(users)
        await ctx.send("limit removed from user")

    @commands.Cog.listener()
    async def on_message(self, message):
        if not message.guild or message.author.bot:
            return

        guild_config = self.config.guild(message.guild)
        users = await guild_config.users()

        uuid = str(message.author.id)
        if uuid not in users:
            return

        udata = users[uuid]
        is_media = self._is_media_msg(message)

        key = "media" if is_media else "text"
        if key not in udata:
            return

        data = udata[key]
        self._reset_24h(data)
        data["count"] += 1

        if data["count"] > data["limit"]:
            try:
                await message.delete()
            except Exception:
                pass
            return

        udata[key] = data
        users[uuid] = udata
        await guild_config.users.set(users)
