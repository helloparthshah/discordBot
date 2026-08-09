import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import clip_buffer
from utils.clip_buffer import DEFAULT_CLIP_SECONDS, WINDOW_SECONDS
from utils.voice_client import MISSING_DEPENDENCY_MESSAGE, RECV_AVAILABLE
from utils.voice_receive import encode, mix_tracks

_log = logging.getLogger(__name__)

# How often to re-establish buffering after a recording or call gives the
# receiver back, and when the bot joins somewhere new.
MAINTAIN_INTERVAL = 20


class Clips(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.maintain.start()

    def cog_unload(self):
        self.maintain.cancel()
        for guild_id in list(clip_buffer.buffers):
            guild = self.bot.get_guild(guild_id)
            if guild is not None:
                clip_buffer.stop(guild)

    @tasks.loop(seconds=MAINTAIN_INTERVAL)
    async def maintain(self):
        """Keep the buffer running wherever it should be.

        Polling rather than reacting to events: it also covers the receiver
        coming free after a recording or call, without those features needing
        to know this one exists.
        """
        for voice_client in list(self.bot.voice_clients):
            clip_buffer.start(voice_client)  # no-ops if off, busy, or running

    @maintain.before_loop
    async def before_maintain(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before, after):
        if member.id != self.bot.user.id:
            return
        if after.channel is None:
            clip_buffer.stop(member.guild)
        elif before.channel != after.channel:
            # moved channels: the old buffer's audio is from somewhere else
            clip_buffer.stop(member.guild)
            clip_buffer.start(member.guild.voice_client)

    @app_commands.command(name="clip",
                          description="Post the last few seconds of voice from this channel")
    @app_commands.describe(
        seconds=f"How far back to grab (default {DEFAULT_CLIP_SECONDS}, max {WINDOW_SECONDS})")
    async def clip(self, inter: discord.Interaction,
                   seconds: app_commands.Range[int, 1, WINDOW_SECONDS] = DEFAULT_CLIP_SECONDS):
        await inter.response.defer()

        if not RECV_AVAILABLE:
            return await inter.followup.send(MISSING_DEPENDENCY_MESSAGE)

        buffer = clip_buffer.get(inter.guild)
        if buffer is None:
            if not clip_buffer.is_enabled(inter.guild):
                return await inter.followup.send(
                    "Clip buffering is switched off here. Turn it on with "
                    "`/clipbuffer enabled:true`.")
            return await inter.followup.send(
                "I'm not buffering anything right now — I need to be sitting in "
                "a voice channel, and not recording or on a call.")

        tracks, captured = buffer.snapshot(seconds)
        if not tracks:
            return await inter.followup.send(
                "Nothing in the buffer — nobody has spoken recently.")

        try:
            file, name = await asyncio.to_thread(self._render, tracks)
        except Exception as exc:
            _log.exception("Failed to render a clip")
            return await inter.followup.send(f"Couldn't put that clip together: {exc}")

        limit = inter.guild.filesize_limit
        if file.getbuffer().nbytes > limit:
            return await inter.followup.send(
                f"That clip is bigger than this server's "
                f"{limit // (1024 * 1024)}MB upload limit. Try fewer seconds.")

        speakers = ", ".join(sorted(tracks))
        await inter.followup.send(
            content=f"Last **{captured:.0f}s** · {speakers}",
            file=discord.File(file, filename=name))

    def _render(self, tracks):
        """Runs in a worker thread — mixing and encoding walk the whole clip."""
        return encode(mix_tracks(tracks), "clip")

    @app_commands.command(name="clipbuffer",
                          description="Turn the rolling clip buffer on or off for this server")
    @app_commands.describe(enabled="Leave blank to see the current setting")
    async def clipbuffer(self, inter: discord.Interaction, enabled: bool = None):
        await inter.response.defer()

        if enabled is None:
            state = "on" if clip_buffer.is_enabled(inter.guild) else "off"
            running = clip_buffer.get(inter.guild) is not None
            detail = "buffering now" if running else "not buffering right now"
            return await inter.followup.send(
                f"Clip buffer is **{state}** here ({detail}). "
                f"Keeps the last {WINDOW_SECONDS}s of voice so `/clip` can grab it.")

        clip_buffer.set_enabled(inter.guild, enabled)
        if enabled:
            clip_buffer.start(inter.guild.voice_client)
            await inter.followup.send(
                f"Clip buffer **on**. While I'm in a voice channel I'll keep the "
                f"last {WINDOW_SECONDS}s of audio in memory so anyone can `/clip` it. "
                f"Nothing is written to disk or kept after I leave.")
        else:
            await inter.followup.send("Clip buffer **off**. I'll stop keeping recent audio.")


async def setup(bot):
    print("Adding Clips")
    await bot.add_cog(Clips(bot))


async def teardown(bot):
    print("Unloaded Clips")
