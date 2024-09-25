import asyncio
import random
import discord
from interactions import Extension, File, GuildVoice, OptionType, Permissions, slash_command, slash_option
from interactions import SlashContext
from interactions import Task, IntervalTrigger


class VoiceUtils(Extension):
    @slash_command(name="record", description="record some audio")
    @slash_option(
        name="time",
        description="time in seconds to record",
        opt_type=OptionType.INTEGER,
        required=False
    )
    async def record(self, ctx: SlashContext, time: int = 10):
        await ctx.defer()
        if not ctx.author.voice:
            return await ctx.send('Join a channel first')

        # check if ctx server is same as voice server
        if ctx.guild_id != ctx.author.voice.channel.guild.id:
            return await ctx.send("I am not in the same server as you")

        if not ctx.voice_state:
            await ctx.author.voice.channel.connect()
        else:
            await ctx.voice_state.move(ctx.author.voice.channel)

        # Start recording
        await ctx.voice_state.start_recording()
        await asyncio.sleep(time)
        await ctx.voice_state.stop_recording()
        await ctx.send(files=[File(file, file_name=f"{ctx.guild.get_member(user_id).nick}.mp3"
                                   ) for user_id, file in ctx.voice_state.recorder.output.items()])

    @slash_command(name="dc", description="Disconnet a user from the voice channel")
    @slash_option(
        name="user",
        description="The user to disconnect",
        opt_type=OptionType.USER,
        required=True
    )
    async def dc(self, ctx=SlashContext, *, user: discord.Member):
        if not ctx.author.has_permission(Permissions.MOVE_MEMBERS):
            await ctx.send("You do not have permission to move members")
            return
        try:
            await user.move(None)
            await ctx.send("Disconnected "+user.mention)
        except Exception as e:
            await ctx.send("Error: "+str(e))

    @slash_command(name="move", description="Move a user to a voice channel")
    @slash_option(
        name="user",
        description="The user to move",
        opt_type=OptionType.USER,
        required=True
    )
    @slash_option(
        name="channel",
        description="The channel to move the user to",
        opt_type=OptionType.CHANNEL,
        required=True
    )
    async def move(self, ctx=SlashContext, *, user: discord.Member, channel: GuildVoice):
        if not ctx.author.has_permission(Permissions.MOVE_MEMBERS):
            await ctx.send("You do not have permission to move members")
            return
        try:
            await user.move(channel.id)
            await ctx.send("Moved "+user.mention+" to #"+channel.name)
        except Exception as e:
            await ctx.send("Error: "+str(e))

    async def move_randomly(self):
        guild = self.bot.get_guild(1030766503949254656)
        channels = await guild.fetch_channels()
        channels = [channel for channel in channels if isinstance(
            channel, GuildVoice)]
        # find channel with most members
        max_members = 0
        channel_with_most_members = channels[0]
        for channel in channels:
            if (len(channel.voice_members) > max_members):
                max_members = len(channel.voice_members)
                channel_with_most_members = channel
        members = channel_with_most_members.voice_members
        if (len(members) > 0):
            # move a random member to a random channel
            channels.remove(channel_with_most_members)
            member = random.choice(members)
            new_channel = random.choice(channels)
            await member.move(new_channel.id)
            return member, new_channel
        return None, None

    @slash_command(name="random_move", description="Randomly move a user to a voice channel")
    async def random_move(self, ctx=SlashContext):
        # check if user has move permissions
        if not ctx.author.has_permission(Permissions.MOVE_MEMBERS):
            await ctx.send("You do not have permission to move members")
            return
        member, new_channel = await self.move_randomly()
        if member:
            await ctx.send("Moved "+member.username+" to "+new_channel.name)
        else:
            await ctx.send("No one to move")

    @slash_command(name="start_auto_move", description="Start moving users randomly every 10 minutes")
    async def start_auto_move(self, ctx=SlashContext):
        self.auto_move_randomly.start()
        await ctx.send("Started moving users randomly every 10 minutes")

    @slash_command(name="stop_auto_move", description="Stop moving users randomly every 10 minutes")
    async def stop_auto_move(self, ctx=SlashContext):
        self.auto_move_randomly.stop()
        await ctx.send("Stopped moving users randomly every 10 minutes")

    @Task.create(IntervalTrigger(minutes=1))
    async def auto_move_randomly(self):
        # 10% chance of moving a user every 10 minutes
        if (random.random() < 1):
            member, new_channel = await self.move_randomly()
            if member:
                print("Moved "+member+" to "+new_channel.name)
            else:
                print("No one to move")


def setup(bot):
    VoiceUtils(bot)
