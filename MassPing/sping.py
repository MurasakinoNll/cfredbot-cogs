import discord
from redbot.core import commands


class SPing(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="sping")
    @commands.is_owner()
    @commands.guild_only()
    async def sping(self, ctx: commands.Context, *, target: str):
        member = await self._resolve_member(ctx, target)
        if member is None:
            await ctx.send(f"Couldn't find a member matching `{target}`.")
            return

        channels = self._channels_with_access(ctx.guild, member)
        if not channels:
            await ctx.send(
                f"{member.display_name} doesn't have read access to any text channel."
            )
            return

        pinged = []
        failed = []
        for channel in channels:
            try:
                await channel.send(f"{member.mention}")
                pinged.append(channel.name)
            except discord.Forbidden:
                failed.append(channel.name)

        summary = f"Pinged {member.display_name} in {len(pinged)} channel(s): {', '.join(pinged)}"
        if failed:
            summary += f"\nFailed in {len(failed)} channel(s) (missing send perms): {', '.join(failed)}"
        await ctx.send(summary)

    async def _resolve_member(self, ctx, target: str):
        try:
            return await commands.MemberConverter().convert(ctx, target)
        except commands.BadArgument:
            return None

    def _channels_with_access(self, guild: discord.Guild, member: discord.Member):
        result = []
        for channel in guild.text_channels:
            perms = channel.permissions_for(member)
            if perms.read_messages:
                result.append(channel)
        return result
