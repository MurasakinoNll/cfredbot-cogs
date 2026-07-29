import discord
from redbot.core import commands, Config

SPING_DELETE_AFTER_SECONDS = (
    1  # default seconds before ping messages self-delete; 0 = don't delete
)


class SPing(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=135792468, force_registration=True
        )
        self.config.register_global(
            excluded_channels=[],
            excluded_categories=[],
        )

    @commands.command(name="sping")
    @commands.is_owner()
    @commands.guild_only()
    async def sping(
        self,
        ctx: commands.Context,
        target: str,
        delete_after: int = SPING_DELETE_AFTER_SECONDS,
    ):
        """
        Ping a user in every channel where they have read access.

        <target> can be a mention, user ID, or name.
        <delete_after> optional, seconds before the ping message is deleted (0 = don't delete).
        """
        member = await self._resolve_member(ctx, target)
        if member is None:
            await ctx.send(f"Couldn't find a member matching `{target}`.")
            return

        channels = await self._channels_with_access(ctx.guild, member)
        if not channels:
            await ctx.send(
                f"{member.display_name} doesn't have read access to any non-excluded text channel."
            )
            return

        pinged = []
        failed = []
        for channel in channels:
            try:
                msg = await channel.send(f"{member.mention}")
                if delete_after > 0:
                    await msg.delete(delay=delete_after)
                pinged.append(channel.name)
            except discord.Forbidden:
                failed.append(channel.name)

        await ctx.send("done")

    @commands.command(name="spingexclude")
    @commands.is_owner()
    async def sping_exclude(self, ctx, target_type: str, target_id: int):
        """
        Exclude a channel or category from sping.

        <target_type> is 'channel' or 'category'.
        """
        target_type = target_type.lower()
        if target_type not in ("channel", "category"):
            await ctx.send("target_type must be `channel` or `category`.")
            return

        key = (
            self.config.excluded_channels
            if target_type == "channel"
            else self.config.excluded_categories
        )
        async with key() as lst:
            if target_id in lst:
                await ctx.send(f"That {target_type} is already excluded.")
                return
            lst.append(target_id)
        await ctx.send(f"Excluded {target_type} `{target_id}` from sping.")

    @commands.command(name="spinginclude")
    @commands.is_owner()
    async def sping_include(self, ctx, target_type: str, target_id: int):
        """
        Remove a previously excluded channel or category.

        <target_type> is 'channel' or 'category'.
        """
        target_type = target_type.lower()
        if target_type not in ("channel", "category"):
            await ctx.send("target_type must be `channel` or `category`.")
            return

        key = (
            self.config.excluded_channels
            if target_type == "channel"
            else self.config.excluded_categories
        )
        async with key() as lst:
            if target_id not in lst:
                await ctx.send(f"That {target_type} is not currently excluded.")
                return
            lst.remove(target_id)
        await ctx.send(f"Removed {target_type} `{target_id}` from the exclusion list.")

    @commands.command(name="spinglist")
    @commands.is_owner()
    async def sping_list(self, ctx):
        """Show currently excluded channels and categories."""
        excluded_channels = await self.config.excluded_channels()
        excluded_categories = await self.config.excluded_categories()

        lines = ["**Excluded channels:**"]
        if not excluded_channels:
            lines.append("(none)")
        for cid in excluded_channels:
            channel = ctx.guild.get_channel(cid) if ctx.guild else None
            lines.append(f"- {channel.mention if channel else cid}")

        lines.append("\n**Excluded categories:**")
        if not excluded_categories:
            lines.append("(none)")
        for cid in excluded_categories:
            category = ctx.guild.get_channel(cid) if ctx.guild else None
            lines.append(f"- {category.name if category else cid}")

        await ctx.send("\n".join(lines))

    async def _resolve_member(self, ctx, target: str):
        try:
            return await commands.MemberConverter().convert(ctx, target)
        except commands.BadArgument:
            return None

    async def _channels_with_access(self, guild: discord.Guild, member: discord.Member):
        excluded_channels = set(await self.config.excluded_channels())
        excluded_categories = set(await self.config.excluded_categories())

        result = []
        for channel in guild.text_channels:
            if channel.id in excluded_channels:
                continue
            if channel.category_id and channel.category_id in excluded_categories:
                continue
            perms = channel.permissions_for(member)
            if perms.read_messages:
                result.append(channel)
        return result
