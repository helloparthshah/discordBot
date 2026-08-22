import asyncio
from io import BytesIO
import typing
import requests
from youtube_search import YoutubeSearch
from utils.youtube_client import fetch_audio, open_video
import os
import tempfile
import discord
from discord import Embed, app_commands
from discord.ext import commands
from utils.audio_player import (play, is_playing, is_paused, remaining_ms,
                                seek as seek_source,
                                stop_user, pause_user, resume_user)
from utils import youtube_radio
from pydub import AudioSegment
from discord.ui.select import BaseSelect

music_queue = {}
now_playing = {}
# The live Now Playing view per guild, so /seek can redraw it.
active_players = {}
# Guilds where the queue running dry should pull in a similar song instead of
# stopping, and what has already been played there so it doesn't go in circles.
autoplay_on = {}
history = {}

# How often the Now Playing embed redraws its progress bar, in seconds.
PROGRESS_REFRESH = 10
# How far the ⏪ / ⏩ buttons jump.
SEEK_STEP_MS = 15_000
# How many songs back autoplay remembers. Long enough that a station doesn't
# double back on itself, short enough that a few hours in it can revisit.
HISTORY_LIMIT = 200
# Candidates to try before giving up on a round of autoplay. Any one of them can
# turn out to be age-gated or region-locked and fail to download.
AUTOPLAY_ATTEMPTS = 4
# Where a song lands while it is being decoded. Not the working directory: a
# download that dies leaves the file behind, and these were turning up as
# untracked junk in the repo.
DOWNLOAD_DIR = tempfile.gettempdir()


def parse_position(value: str) -> tuple[typing.Optional[float], bool]:
    """Read '1:47', '90', '+15' or '-30' as (seconds, is_relative)."""
    text = (value or "").strip()
    relative = text.startswith(("+", "-"))
    sign = -1 if text.startswith("-") else 1
    text = text.lstrip("+-").strip()
    if not text:
        return None, False

    parts = text.split(":")
    if len(parts) > 3:
        return None, False
    try:
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + float(part)
    except ValueError:
        return None, False
    return sign * seconds, relative


def build_song(url, requester) -> "MusicQueueSong":
    """Create a song, having proved YouTube will actually serve it. Several
    blocking round trips, so this runs in a worker thread."""
    song = MusicQueueSong(url, requester)
    song.yt.title
    return song


def format_duration(seconds) -> str:
    seconds = max(0, int(seconds or 0))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def remember(guild_id: int, video_id: str) -> None:
    """Note that a song played here, so autoplay won't come back to it."""
    if not video_id:
        return
    played = history.setdefault(guild_id, [])
    if video_id in played:
        played.remove(video_id)
    played.append(video_id)
    del played[:-HISTORY_LIMIT]


def progress_bar(elapsed_ms: float, total_ms: float, length: int = 14) -> str:
    if not total_ms:
        return "▬" * length
    fraction = min(1.0, max(0.0, elapsed_ms / total_ms))
    knob = min(length - 1, int(fraction * length))
    return "▬" * knob + "🔘" + "▬" * (length - knob - 1)

class BaseView(discord.ui.View):
    interaction: discord.Interaction | None = None
    message: discord.Message | None = None

    def __init__(self, timeout: float = None):
        super().__init__(timeout=timeout)

    def _disable_all(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button) or isinstance(item, BaseSelect):
                item.disabled = True

    async def _edit(self, **kwargs: typing.Any) -> None:
        if self.interaction is None and self.message is not None:
            await self.message.edit(**kwargs)
        elif self.interaction is not None:
            try:
                await self.interaction.response.edit_message(**kwargs)
            except discord.InteractionResponded:
                await self.interaction.edit_original_response(**kwargs)

    async def on_timeout(self) -> None:
        self._disable_all()
        await self._edit(view=self)


class MusicQueueSong:
    def __init__(self, url, requester: discord.abc.User = None):
        self.url = url
        self.requester = requester
        # Which client YouTube will serve is a coin toss that changes minute to
        # minute, so this retries across a couple of them — see utils/youtube_client.
        self.yt = open_video(url)


class MusicPlayerView(BaseView):
    """The Now Playing message: an embed that redraws its own progress bar,
    plus the transport controls."""

    def __init__(self, cog: "MusicCommands", inter: discord.Interaction,
                 song: MusicQueueSong, total_ms: int):
        super().__init__()
        self.cog = cog
        self.origin = inter
        self.song = song
        self.total_ms = total_ms
        self.identifier = cog.generate_music_identitiy(inter)
        self.started = False
        self.finished = False

    # --- rendering -------------------------------------------------------

    @property
    def paused(self) -> bool:
        return is_paused(self.origin, self.identifier)

    def elapsed_ms(self) -> int:
        left = remaining_ms(self.origin, self.identifier)
        if left is None:
            # no segment loaded: either we haven't handed it to the mixer yet,
            # or it has been fully consumed
            return self.total_ms if self.started else 0
        return max(0, self.total_ms - left)

    def render(self) -> Embed:
        # The buttons are part of the drawing, so they're brought up to date
        # here rather than by each caller: the very first send didn't sync them,
        # and a player created mid-station showed a grey Autoplay button until
        # the progress bar happened to tick.
        self._sync_buttons()

        yt = self.song.yt
        upcoming = music_queue.get(self.origin.guild.id, [])

        if self.finished:
            header, colour = "Finished playing", 0x2b2d31
        elif self.paused:
            header, colour = "⏸  Paused", 0xfaa61a
        else:
            header, colour = "♪  Now Playing", 0x1db954

        embed = Embed(title=yt.title, url=self.song.url, colour=colour)
        embed.set_author(name=header)
        embed.set_thumbnail(url=yt.thumbnail_url)

        total = format_duration(self.total_ms / 1000)
        if self.finished:
            embed.description = f"by **{yt.author}**\n`{total}`"
        else:
            elapsed = format_duration(self.elapsed_ms() / 1000)
            bar = progress_bar(self.elapsed_ms(), self.total_ms)
            embed.description = f"by **{yt.author}**\n{bar} `{elapsed} / {total}`"

        if upcoming:
            nxt = upcoming[0]
            more = f" (+{len(upcoming) - 1} more)" if len(upcoming) > 1 else ""
            embed.add_field(name="Up next",
                            value=f"[{nxt.yt.title}]({nxt.url}){more}",
                            inline=False)
        elif autoplay_on.get(self.origin.guild.id) and not self.finished:
            embed.add_field(name="Up next",
                            value="♾  Autoplay — I'll find something similar",
                            inline=False)

        queued = f" · {len(upcoming)} in queue" if upcoming else ""
        if self.song.requester:
            embed.set_footer(text=f"Requested by {self.song.requester.display_name}{queued}",
                             icon_url=self.song.requester.display_avatar.url)
        else:
            # nobody asked for this one: autoplay picked it
            embed.set_footer(text=f"Picked by autoplay{queued}")
        return embed

    def _sync_buttons(self) -> None:
        if self.finished:
            self._disable_all()
            return
        if self.paused:
            self.pause_resume.label, self.pause_resume.emoji = "Resume", "▶️"
        else:
            self.pause_resume.label, self.pause_resume.emoji = "Pause", "⏸"
        on = autoplay_on.get(self.origin.guild.id, False)
        self.autoplay.style = (discord.ButtonStyle.success if on
                               else discord.ButtonStyle.secondary)

    def mark_finished(self) -> None:
        self.finished = True
        self._sync_buttons()

    async def refresh(self, inter: discord.Interaction = None) -> None:
        """Redraw the message, either as a response to a click or on our own."""
        try:
            if inter is not None:
                await inter.response.edit_message(embed=self.render(), view=self)
            elif self.message is not None:
                await self.message.edit(embed=self.render(), view=self)
        except discord.HTTPException:
            pass

    # --- controls --------------------------------------------------------

    def seek_by(self, delta_ms: int) -> None:
        seek_source(self.origin, self.identifier, self.elapsed_ms() + delta_ms)

    @discord.ui.button(emoji="⏪", style=discord.ButtonStyle.secondary)
    async def rewind(self, inter: discord.Interaction, button: discord.ui.Button):
        self.seek_by(-SEEK_STEP_MS)
        await self.refresh(inter)

    @discord.ui.button(label="Pause", emoji="⏸", style=discord.ButtonStyle.secondary)
    async def pause_resume(self, inter: discord.Interaction, button: discord.ui.Button):
        if self.paused:
            resume_user(self.origin, self.identifier)
        else:
            pause_user(self.origin, self.identifier)
        await self.refresh(inter)

    @discord.ui.button(emoji="⏩", style=discord.ButtonStyle.secondary)
    async def forward(self, inter: discord.Interaction, button: discord.ui.Button):
        self.seek_by(SEEK_STEP_MS)
        await self.refresh(inter)

    @discord.ui.button(label="Skip", emoji="⏭", style=discord.ButtonStyle.secondary)
    async def skip(self, inter: discord.Interaction, button: discord.ui.Button):
        self.mark_finished()
        await self.refresh(inter)
        stop_user(self.origin, self.identifier)

    @discord.ui.button(label="Stop", emoji="⏹", style=discord.ButtonStyle.danger)
    async def stop_playback(self, inter: discord.Interaction, button: discord.ui.Button):
        music_queue[self.origin.guild.id] = []
        # Stop has to mean stop: leaving autoplay on would refill the queue.
        autoplay_on[self.origin.guild.id] = False
        self.mark_finished()
        await self.refresh(inter)
        stop_user(self.origin, self.identifier)

    @discord.ui.button(label="Autoplay", emoji="♾", row=1,
                       style=discord.ButtonStyle.secondary)
    async def autoplay(self, inter: discord.Interaction, button: discord.ui.Button):
        guild_id = self.origin.guild.id
        turned_on = not autoplay_on.get(guild_id, False)
        autoplay_on[guild_id] = turned_on
        await self.refresh(inter)
        if turned_on and not is_playing(self.origin, self.identifier):
            # Switched on after the last song ended: the loop that would have
            # picked up the next one has already exited, so start a new one.
            await self.cog.play_next(self.origin)


class MusicCommands(commands.Cog):
    def __init__(self, bot=None):
        self.bot = bot
        # Guilds whose play loop is already running. Between songs nothing is
        # playing but the loop is still live, and a second one would race it.
        self.advancing: set[int] = set()

    def generate_music_identitiy(self, inter: discord.Interaction):
        return str(inter.guild.id) + "-music"
    
    async def autocomplete_link(self,  inter: discord.Interaction, current: str):
        string_option_input = current
        if not string_option_input or len(string_option_input) < 3:
            return []
        results = self.search_youtube(string_option_input)
        print(results)
        choices = []
        for result in results:
            clean_url_suffix = result['url_suffix'].split('&')[0]
            choices.append(
                app_commands.Choice[str](name=result['title'],
                                         value='https://www.youtube.com'+clean_url_suffix))
        return choices
    
    @app_commands.command(name="play", description="play a song!")
    @app_commands.describe(
        link="The song to play",
        autoplay="Keep going with similar songs when the queue runs out",
    )
    @app_commands.autocomplete(link=autocomplete_link)
    async def play(self, inter: discord.Interaction, link: str,
                   autoplay: typing.Optional[bool] = None):
        await inter.response.defer()
        try:
            # left out, the setting stays as it was — otherwise every /play
            # during a station would quietly switch it back off
            if autoplay is not None:
                autoplay_on[inter.guild.id] = autoplay

            # check if link is a youtube link
            if "youtube.com" not in link:
                suffix = self.search_youtube(link)[0]['url_suffix'].split('&')[0]
                link = 'https://www.youtube.com' + suffix

            song = await asyncio.to_thread(build_song, link, inter.user)
            music_queue[inter.guild.id] = music_queue.get(inter.guild.id, [])
            music_queue[inter.guild.id].append(song)

            # add to queue if something is already playing, or if the play loop
            # is between songs — it will pick this up on its own
            if (is_playing(inter, self.generate_music_identitiy(inter))
                    or inter.guild.id in self.advancing):
                position = len(music_queue[inter.guild.id])
                embed = Embed(title=song.yt.title, url=song.url, colour=0x5865f2)
                embed.set_author(name="＋  Added to queue")
                embed.set_thumbnail(url=song.yt.thumbnail_url)
                embed.description = (f"by **{song.yt.author}** · "
                                     f"`{format_duration(song.yt.length)}`")
                embed.set_footer(text=f"#{position} in queue · "
                                      f"requested by {inter.user.display_name}",
                                 icon_url=inter.user.display_avatar.url)
                return await inter.followup.send(embed=embed)

            await self.play_next(inter)
        except Exception as e:
            print(e)
            await inter.followup.send(f"Couldn't play that: {e}")

    async def announce(self, inter: discord.Interaction, **kwargs):
        """Post a message for this session.

        An interaction's webhook token dies after 15 minutes and an autoplay
        station outlives that many times over, so once it's stale — or if the
        followup fails for any other reason — fall back to a plain message in
        the channel.
        """
        age = (discord.utils.utcnow() - inter.created_at).total_seconds()
        if age < 14 * 60:
            try:
                return await inter.followup.send(wait=True, **kwargs)
            except discord.HTTPException:
                pass
        try:
            return await inter.channel.send(**kwargs)
        except (discord.HTTPException, AttributeError):
            return None

    async def play_next(self, inter: discord.Interaction):
        """Work through the queue, extending it with similar songs if autoplay
        is on.

        A loop rather than a tail call: with autoplay this runs for hours, and
        recursing per song kept every finished song's decoded audio alive in a
        parent frame — tens of megabytes each.
        """
        guild_id = inter.guild.id
        if guild_id in self.advancing:
            return  # a loop is already draining this queue
        self.advancing.add(guild_id)
        try:
            while True:
                if not music_queue.get(guild_id):
                    if not await self.extend_autoplay(inter):
                        return
                if not music_queue.get(guild_id):
                    return

                song = music_queue[guild_id].pop(0)
                remember(guild_id, getattr(song.yt, 'video_id', None))
                try:
                    await self.play_song(inter, song)
                except Exception as exc:
                    # One bad track mustn't end the session. YouTube throttles
                    # and 403s the occasional stream, and over a station that
                    # runs for hours that's a matter of when, not if.
                    print(f"Skipping {song.url}: {exc}")
                    now_playing.pop(guild_id, None)
                    await self.announce(
                        inter, content=f"⚠  Couldn't play **{song.yt.title}**, "
                                       f"skipping it.")
        finally:
            self.advancing.discard(guild_id)

    async def play_song(self, inter: discord.Interaction, song: "MusicQueueSong"):
        now_playing[inter.guild.id] = song
        identifier = self.generate_music_identitiy(inter)

        yt = song.yt

        # Named for the guild and the video rather than for the title: two
        # guilds can want the same song at once, and YouTube titles are full of
        # characters Windows won't put in a filename.
        out_file = os.path.join(DOWNLOAD_DIR, f"{inter.guild.id}-{yt.video_id}.m4a")
        try:
            # extract only audio — both the download and the decode are
            # blocking, so keep them off the event loop or the buttons stop
            # responding. fetch_audio may hand back a different handle, having
            # reopened the video on a client that would actually serve it.
            song.yt = await asyncio.to_thread(fetch_audio, yt, out_file)
            audio = await asyncio.to_thread(AudioSegment.from_file, out_file)
        finally:
            # pydub has the whole thing in memory now
            if os.path.exists(out_file):
                os.remove(out_file)

        view = MusicPlayerView(self, inter, song, len(audio))
        active_players[inter.guild.id] = view
        view.message = await self.announce(inter, embed=view.render(), view=view)

        await play(inter, audio, identifier)
        view.started = True

        # tick the progress bar while the song plays
        elapsed_ticks = 0
        while is_playing(inter, identifier):
            await asyncio.sleep(1)
            elapsed_ticks += 1
            if elapsed_ticks % PROGRESS_REFRESH == 0 and not view.finished:
                await view.refresh()

        view.mark_finished()
        await view.refresh()
        now_playing.pop(inter.guild.id, None)
        if active_players.get(inter.guild.id) is view:
            del active_players[inter.guild.id]

    async def extend_autoplay(self, inter: discord.Interaction) -> bool:
        """Queue one song like the last one. False when the station should end."""
        guild_id = inter.guild.id
        if not autoplay_on.get(guild_id):
            return False
        if inter.guild.voice_client is None:
            # Kicked, /leave, or the idle timer. Don't drag the bot back in.
            return False

        played = history.get(guild_id) or []
        if not played:
            return False

        candidates = await asyncio.to_thread(youtube_radio.pick_next,
                                             played[-1], set(played))
        for candidate in candidates[:AUTOPLAY_ATTEMPTS]:
            try:
                song = await asyncio.to_thread(build_song, candidate.url, None)
            except Exception as exc:
                # Age-gated, region-locked, or pulled since the mix was built.
                print(f"Autoplay skipped {candidate.video_id}: {exc}")
                remember(guild_id, candidate.video_id)
                continue
            music_queue.setdefault(guild_id, []).append(song)
            return True

        await self.announce(
            inter, content="♾  Autoplay couldn't find another song to play, "
                           "so I've stopped here.")
        autoplay_on[guild_id] = False
        return False

    @app_commands.command(name="skip", description="Skip the current song")
    async def skip(self, inter: discord.Interaction):
        await inter.response.defer()
        if not is_playing(inter, self.generate_music_identitiy(inter)):
            return await inter.followup.send('Nothing is playing')
        stop_user(inter, self.generate_music_identitiy(inter))
        await inter.followup.send("⏭  Skipped the current song")

    @app_commands.command(name="seek", description="Jump to a position in the current song")
    @app_commands.describe(position="Where to jump to: 1:47, 90, +15, -30")
    async def seek(self, inter: discord.Interaction, position: str):
        await inter.response.defer()
        identifier = self.generate_music_identitiy(inter)
        if not is_playing(inter, identifier):
            return await inter.followup.send("Nothing is playing")

        seconds, relative = parse_position(position)
        if seconds is None:
            return await inter.followup.send(
                "I couldn't read that position. Try `1:47`, `90`, `+15` or `-30`.")

        view = active_players.get(inter.guild.id)
        target_ms = seconds * 1000
        if relative:
            elapsed = view.elapsed_ms() if view else (remaining_ms(inter, identifier) or 0)
            target_ms += elapsed

        landed = seek_source(inter, identifier, target_ms)
        if landed is None:
            return await inter.followup.send("There's nothing seekable playing")

        if view is not None:
            await view.refresh()
        await inter.followup.send(f"⏩  Jumped to `{format_duration(landed / 1000)}`")

    def search_youtube(self, query):
        return YoutubeSearch(query, max_results=5).to_dict()

    @app_commands.command(name="queue", description="Show the current queue")
    async def queue(self, inter: discord.Interaction):
        await inter.response.defer()
        current = now_playing.get(inter.guild.id)
        upcoming = music_queue.get(inter.guild.id, [])
        if not current and not upcoming:
            return await inter.followup.send('No songs in queue')

        embed = Embed(title="Queue", colour=0x1db954)
        if current:
            embed.description = (f"**Now playing**\n"
                                 f"[{current.yt.title}]({current.url}) · "
                                 f"`{format_duration(current.yt.length)}`")
            embed.set_thumbnail(url=current.yt.thumbnail_url)

        if upcoming:
            # embeds cap at 25 fields, and a huge queue is unreadable anyway
            lines = [f"`{i + 1}.` [{song.yt.title}]({song.url}) · "
                     f"`{format_duration(song.yt.length)}`"
                     for i, song in enumerate(upcoming[:10])]
            if len(upcoming) > 10:
                lines.append(f"…and {len(upcoming) - 10} more")
            embed.add_field(name=f"Up next ({len(upcoming)})",
                            value="\n".join(lines), inline=False)
            total = sum(song.yt.length or 0 for song in upcoming)
            embed.set_footer(text=f"{format_duration(total)} of queued audio")

        if autoplay_on.get(inter.guild.id):
            embed.add_field(name="Autoplay",
                            value="♾  On — similar songs keep coming when the "
                                  "queue empties",
                            inline=False)

        await inter.followup.send(embed=embed)

    @app_commands.command(name="stop", description="Stop the audio")
    async def stop(self, inter: discord.Interaction):
        await inter.response.defer()
        music_queue[inter.guild.id] = []
        # otherwise autoplay refills the queue and it never actually stops
        autoplay_on[inter.guild.id] = False
        stop_user(inter, self.generate_music_identitiy(inter))
        await inter.followup.send('⏹  Stopped the audio')

    @app_commands.command(name="pause", description="Pause the audio")
    async def pause(self, inter: discord.Interaction):
        await inter.response.defer()
        if not is_playing(inter, self.generate_music_identitiy(inter)):
            return await inter.followup.send('Nothing is playing')
        pause_user(inter, self.generate_music_identitiy(inter))
        await inter.followup.send('⏸  Paused the audio')

    @app_commands.command(name="resume", description="Resume the audio")
    async def resume(self, inter: discord.Interaction):
        await inter.response.defer()
        if not is_playing(inter, self.generate_music_identitiy(inter)):
            return await inter.followup.send('No audio to resume')
        resume_user(inter, self.generate_music_identitiy(inter))
        await inter.followup.send('▶️  Resumed the audio')

    @app_commands.command(name="play_file", description="Play an audio file")
    @app_commands.describe(
        file="Upload file to play",
    )
    async def play_file(self, inter: discord.Interaction, file: discord.Attachment):
        await inter.response.defer()
        print("Playing "+file.url)
        res = requests.get(file.url)
        audio = AudioSegment.from_file(BytesIO(res.content), format=file.filename.split('.')[-1])
        await play(inter, audio, self.generate_music_identitiy(inter))
        await inter.followup.send("Playing file " + file.url)


async def setup(bot):
    print("Adding MusicCommands")
    await bot.add_cog(MusicCommands(bot))


async def teardown(bot):
    print("Unloaded MusicCommands")
