"""Cross-server voice bridge.

/call rings another server the bot is in. If someone there accepts, the bot sits
in a voice channel on each side and relays audio between them.

Each remote speaker becomes a live source in the far guild's AudioPlayer, so the
mixer that already handles music-over-soundboard does the actual mixing — a
bridge is just plumbing between a receiver on one side and a source on the other.
"""
import logging
import threading
import time

import discord
from discord import Embed, app_commands
from discord.ext import commands

from utils.audio_player import (close_stream, connect_to, disconnect_voice,
                                feed_stream, open_stream)
from utils import clip_buffer
from utils.voice_client import MISSING_DEPENDENCY_MESSAGE, RECV_AVAILABLE, voice_recv
from utils.voice_receive import PacketDecoder, drain_socket

_log = logging.getLogger(__name__)

RING_TIMEOUT = 60
# Guilds are keyed by id; one call per guild at a time.
calls: dict[int, "Call"] = {}
ringing: dict[int, int] = {}  # target guild id -> caller guild id


def source_id(user_id: int) -> str:
    return f"call:{user_id}"


class BridgeSide:
    """Receives in one guild, plays into the other."""

    def __init__(self, guild: discord.Guild, voice_client, destination: discord.Guild):
        self.guild = guild
        self.voice_client = voice_client
        self.destination = destination
        self.decoder = PacketDecoder(voice_client)
        self.speakers: set[str] = set()
        self._lock = threading.Lock()

    def start(self) -> None:
        # The clip buffer holds the receiver while the bot is idle; take it.
        clip_buffer.stop(self.guild)
        # Nothing has read this socket since the last listen, so the kernel may
        # be holding a backlog that would arrive as a burst of stale audio.
        drain_socket(self.voice_client)
        self.voice_client.listen(voice_recv.BasicSink(self.write, decode=False))

    def stop(self) -> None:
        try:
            self.voice_client.stop_listening()
        except Exception:
            _log.debug("stop_listening failed on %s", self.guild.id, exc_info=True)
        with self._lock:
            speakers, self.speakers = self.speakers, set()
        for identifier in speakers:
            close_stream(self.destination, identifier)

    def write(self, user, data) -> None:
        """Sink callback, on the reader thread. Never raise: that kills it."""
        try:
            self._write(user, data)
        except Exception:
            _log.exception("Dropped a packet bridging %s", self.guild.id)

    def _write(self, user, data) -> None:
        if user is None:
            return
        pcm = data.pcm or self.decoder.to_pcm(user.id, data)
        if not pcm:
            return

        identifier = source_id(user.id)
        with self._lock:
            first_time = identifier not in self.speakers
            if first_time:
                self.speakers.add(identifier)

        if first_time and not open_stream(self.destination, identifier):
            with self._lock:  # far side has no player at all
                self.speakers.discard(identifier)
            return

        if not feed_stream(self.destination, identifier, pcm):
            # The far guild's AudioPlayer was replaced (someone re-ran a voice
            # command and it rebuilt), taking our registration with it.
            if open_stream(self.destination, identifier):
                feed_stream(self.destination, identifier, pcm)
            else:
                with self._lock:
                    self.speakers.discard(identifier)


class Call:
    def __init__(self, a: discord.Guild, b: discord.Guild):
        self.guilds = (a, b)
        self.sides: list[BridgeSide] = []
        self.started = time.time()

    def open(self) -> None:
        a, b = self.guilds
        self.sides = [
            BridgeSide(a, a.voice_client, b),
            BridgeSide(b, b.voice_client, a),
        ]
        for side in self.sides:
            side.start()
        for guild in self.guilds:
            calls[guild.id] = self

    def close(self) -> None:
        for side in self.sides:
            side.stop()
        self.sides = []
        for guild in self.guilds:
            if calls.get(guild.id) is self:
                del calls[guild.id]

    def other(self, guild: discord.Guild) -> discord.Guild:
        a, b = self.guilds
        return b if guild.id == a.id else a


class RingView(discord.ui.View):
    """Accept/Decline shown on the receiving side."""

    def __init__(self, cog: "CallCommands", caller: discord.Member,
                 caller_channel: discord.VoiceChannel, target_id: int):
        super().__init__(timeout=RING_TIMEOUT)
        self.cog = cog
        self.caller = caller
        self.caller_channel = caller_channel
        self.target_id = target_id
        self.target_name = "the other server"
        self.message: discord.Message | None = None
        self.answered = False

    def _close(self) -> None:
        ringing.pop(self.target_id, None)
        self.answered = True
        for item in self.children:
            item.disabled = True

    async def _tell_caller(self, message: str) -> None:
        try:
            await self.caller_channel.send(message)
        except (discord.HTTPException, AttributeError):
            _log.debug("Couldn't notify the caller", exc_info=True)

    async def on_timeout(self) -> None:
        if self.answered:
            return
        self._close()
        if self.message:
            embed = self.message.embeds[0]
            embed.colour = 0x2b2d31
            embed.set_footer(text="No answer")
            try:
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass
        await self._tell_caller(
            f"{self.caller.mention} nobody answered in **{self.target_name}**.")

    @discord.ui.button(label="Accept", emoji="📞", style=discord.ButtonStyle.success)
    async def accept(self, inter: discord.Interaction, button: discord.ui.Button):
        if not isinstance(inter.user, discord.Member) or not inter.user.voice:
            return await inter.response.send_message(
                "Join a voice channel first, then accept.", ephemeral=True)
        self._close()
        await inter.response.edit_message(view=self)
        await self.cog.connect_call(inter, self.caller, self.caller_channel,
                                    inter.user.voice.channel)

    @discord.ui.button(label="Decline", emoji="✖", style=discord.ButtonStyle.danger)
    async def decline(self, inter: discord.Interaction, button: discord.ui.Button):
        self._close()
        await inter.response.edit_message(view=self)
        await inter.followup.send(f"Declined the call from **{self.caller.guild.name}**.")
        await self._tell_caller(
            f"{self.caller.mention} your call to **{inter.guild.name}** was declined.")


class CallCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # --- helpers ---------------------------------------------------------

    def reachable_guilds(self, exclude: discord.Guild) -> list[discord.Guild]:
        return [g for g in self.bot.guilds if g.id != exclude.id]

    def invite_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        """Somewhere the bot may post the incoming-call notice."""
        candidates = []
        if guild.system_channel:
            candidates.append(guild.system_channel)
        candidates.extend(guild.text_channels)
        for channel in candidates:
            perms = channel.permissions_for(guild.me)
            if perms.send_messages and perms.view_channel:
                return channel
        return None

    async def autocomplete_server(self, inter: discord.Interaction, current: str):
        current = (current or "").lower()
        matches = [g for g in self.reachable_guilds(inter.guild)
                   if current in g.name.lower()]
        return [app_commands.Choice(name=g.name[:100], value=str(g.id))
                for g in matches[:25]]

    # --- commands --------------------------------------------------------

    @app_commands.command(name="call", description="Call another server the bot is in")
    @app_commands.describe(server="Which server to ring")
    @app_commands.autocomplete(server=autocomplete_server)
    async def call(self, inter: discord.Interaction, server: str):
        await inter.response.defer()

        if not RECV_AVAILABLE:
            return await inter.followup.send(MISSING_DEPENDENCY_MESSAGE)

        if not isinstance(inter.user, discord.Member) or not inter.user.voice:
            return await inter.followup.send("Join a voice channel first.")

        if inter.guild.id in calls:
            return await inter.followup.send(
                "This server is already on a call. Use /hangup first.")

        try:
            target = self.bot.get_guild(int(server))
        except ValueError:
            target = discord.utils.get(self.bot.guilds, name=server)
        if target is None or target.id == inter.guild.id:
            return await inter.followup.send("I couldn't find that server.")

        if target.id in calls:
            return await inter.followup.send(f"**{target.name}** is already on a call.")
        if target.id in ringing:
            return await inter.followup.send(f"**{target.name}** is already ringing.")

        channel = self.invite_channel(target)
        if channel is None:
            return await inter.followup.send(
                f"I can't post in **{target.name}** — no channel I'm allowed to write to.")

        embed = Embed(title="📞  Incoming call", colour=0x5865f2,
                      description=f"**{inter.guild.name}** is calling.\n"
                                  f"Join a voice channel and accept to connect "
                                  f"the two servers.")
        embed.set_footer(text=f"Started by {inter.user.display_name} · "
                              f"rings for {RING_TIMEOUT}s",
                         icon_url=inter.user.display_avatar.url)
        if inter.guild.icon:
            embed.set_thumbnail(url=inter.guild.icon.url)

        view = RingView(self, inter.user, inter.user.voice.channel, target.id)
        view.target_name = target.name
        ringing[target.id] = inter.guild.id
        try:
            view.message = await channel.send(embed=embed, view=view)
        except discord.HTTPException as exc:
            ringing.pop(target.id, None)
            return await inter.followup.send(f"Couldn't ring that server: {exc}")

        await inter.followup.send(
            f"Ringing **{target.name}** in {channel.mention}… "
            f"stay in {inter.user.voice.channel.mention}.")

    async def connect_call(self, inter: discord.Interaction, caller: discord.Member,
                           caller_channel: discord.VoiceChannel,
                           target_channel: discord.VoiceChannel):
        """Both sides agreed: join each channel and wire the bridges up."""
        ringing.pop(inter.guild.id, None)
        caller_guild = caller_channel.guild

        # The caller may have wandered off while it rang.
        if not caller.voice or not caller.voice.channel:
            return await inter.followup.send(
                f"{caller.display_name} left the voice channel — call cancelled.")
        caller_channel = caller.voice.channel

        try:
            if not await connect_to(caller_channel):
                raise RuntimeError(f"couldn't join {caller_channel} in {caller_guild}")
            if not await connect_to(target_channel):
                raise RuntimeError(f"couldn't join {target_channel} in {inter.guild}")
        except Exception as exc:
            _log.exception("Failed to connect a call")
            return await inter.followup.send(f"Couldn't connect the call: {exc}")

        for guild in (caller_guild, inter.guild):
            vc = guild.voice_client
            if not isinstance(vc, voice_recv.VoiceRecvClient):
                return await inter.followup.send(
                    f"I'm in **{guild.name}** with a voice client that can't receive. "
                    f"Disconnect me there and try again.")
            # A guild has one receiver, so a call can't share with a recording.
            if vc.is_listening():
                return await inter.followup.send(
                    f"I'm already receiving audio in **{guild.name}** "
                    f"(a recording or another call). Finish that first.")

        call = Call(caller_guild, inter.guild)
        try:
            call.open()
        except Exception as exc:
            _log.exception("Failed to open a call")
            call.close()
            return await inter.followup.send(f"Couldn't connect the call: {exc}")

        await inter.followup.send(
            embed=self._connected_embed(caller_guild, inter.guild, target_channel))
        try:
            await caller_channel.send(
                embed=self._connected_embed(inter.guild, caller_guild, caller_channel))
        except discord.HTTPException:
            pass

    def _connected_embed(self, other: discord.Guild, here: discord.Guild,
                         channel: discord.VoiceChannel) -> Embed:
        embed = Embed(title="📞  Call connected", colour=0x1db954,
                      description=f"Connected to **{other.name}**.\n"
                                  f"Anyone in {channel.mention} can talk to them.")
        embed.set_footer(text="/hangup to end the call")
        return embed

    @app_commands.command(name="hangup",
                          description="Drop the call and leave both voice channels")
    async def hangup(self, inter: discord.Interaction):
        await inter.response.defer()
        call = calls.get(inter.guild.id)
        if call is None:
            return await inter.followup.send("This server isn't on a call.")

        other = call.other(inter.guild)
        await self.drop(call)
        await inter.followup.send(f"📴  Call with **{other.name}** ended.")

        channel = self.invite_channel(other)
        if channel is not None:
            try:
                await channel.send(f"📴  **{inter.guild.name}** hung up.")
            except discord.HTTPException:
                pass

    async def drop(self, call: "Call") -> None:
        """Tear the bridge down and leave voice on both sides."""
        guilds = call.guilds
        call.close()
        for guild in guilds:
            try:
                await disconnect_voice(guild)
            except Exception:
                _log.exception("Failed to leave voice in %s", guild.id)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before, after):
        """Drop the call if the bot gets disconnected from either side."""
        if member.id != self.bot.user.id or after.channel is not None:
            return
        call = calls.get(member.guild.id)
        if call is not None:
            # Half a bridge is no use to anyone — take the other side down too.
            _log.info("Bot left voice in %s, ending the call", member.guild.id)
            await self.drop(call)


async def setup(bot):
    print("Adding CallCommands")
    await bot.add_cog(CallCommands(bot))


async def teardown(bot):
    for call in list(calls.values()):
        call.close()
    print("Unloaded CallCommands")
