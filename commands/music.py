import asyncio
from io import BytesIO
import typing
import requests
from youtube_search import YoutubeSearch
from pytubefix import YouTube
import os
import discord
from discord import Embed, app_commands
from discord.ext import commands
from utils.audio_player import (play, is_playing, is_paused, remaining_ms,
                                stop_user, pause_user, resume_user)
from pydub import AudioSegment
from discord.ui.select import BaseSelect

music_queue = {}
now_playing = {}

# How often the Now Playing embed redraws its progress bar, in seconds.
PROGRESS_REFRESH = 10


def build_song(url, requester) -> "MusicQueueSong":
    """Create a song and warm its metadata. pytubefix fetches lazily on first
    attribute access, so touch it here — this runs in a worker thread."""
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
        # The WEB client needs a poToken (botGuard/node) to get playable stream
        # urls; without it YouTube returns streams with no url and pytubefix
        # blows up with UnboundLocalError. ANDROID_VR (pytubefix's default)
        # doesn't require one.
        self.yt = YouTube(url)


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

        if self.song.requester:
            queued = f" · {len(upcoming)} in queue" if upcoming else ""
            embed.set_footer(text=f"Requested by {self.song.requester.display_name}{queued}",
                             icon_url=self.song.requester.display_avatar.url)
        return embed

    def _sync_buttons(self) -> None:
        if self.finished:
            self._disable_all()
            return
        if self.paused:
            self.pause_resume.label, self.pause_resume.emoji = "Resume", "▶️"
        else:
            self.pause_resume.label, self.pause_resume.emoji = "Pause", "⏸"

    def mark_finished(self) -> None:
        self.finished = True
        self._sync_buttons()

    async def refresh(self, inter: discord.Interaction = None) -> None:
        """Redraw the message, either as a response to a click or on our own."""
        self._sync_buttons()
        try:
            if inter is not None:
                await inter.response.edit_message(embed=self.render(), view=self)
            elif self.message is not None:
                await self.message.edit(embed=self.render(), view=self)
        except discord.HTTPException:
            pass

    # --- controls --------------------------------------------------------

    @discord.ui.button(label="Pause", emoji="⏸", style=discord.ButtonStyle.secondary)
    async def pause_resume(self, inter: discord.Interaction, button: discord.ui.Button):
        if self.paused:
            resume_user(self.origin, self.identifier)
        else:
            pause_user(self.origin, self.identifier)
        await self.refresh(inter)

    @discord.ui.button(label="Skip", emoji="⏭", style=discord.ButtonStyle.secondary)
    async def skip(self, inter: discord.Interaction, button: discord.ui.Button):
        self.mark_finished()
        await self.refresh(inter)
        stop_user(self.origin, self.identifier)

    @discord.ui.button(label="Stop", emoji="⏹", style=discord.ButtonStyle.danger)
    async def stop_playback(self, inter: discord.Interaction, button: discord.ui.Button):
        music_queue[self.origin.guild.id] = []
        self.mark_finished()
        await self.refresh(inter)
        stop_user(self.origin, self.identifier)


class MusicCommands(commands.Cog):
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
        link="The song to play"
    )
    @app_commands.autocomplete(link=autocomplete_link)
    async def play(self, inter: discord.Interaction, link: str):
        await inter.response.defer()
        try:
            # check if link is a youtube link
            if "youtube.com" not in link:
                suffix = self.search_youtube(link)[0]['url_suffix'].split('&')[0]
                link = 'https://www.youtube.com' + suffix

            song = await asyncio.to_thread(build_song, link, inter.user)
            music_queue[inter.guild.id] = music_queue.get(inter.guild.id, [])
            music_queue[inter.guild.id].append(song)

            # add to queue if already playing
            if is_playing(inter, self.generate_music_identitiy(inter)):
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

    async def play_next(self,  inter: discord.Interaction):
        current_song = music_queue[inter.guild.id].pop(0)
        now_playing[inter.guild.id] = current_song
        identifier = self.generate_music_identitiy(inter)

        yt = current_song.yt

        # extract only audio — both the download and the decode are blocking,
        # so keep them off the event loop or the buttons stop responding
        video = await asyncio.to_thread(lambda: yt.streams.get_audio_only())
        out_file = await asyncio.to_thread(video.download, output_path='.')
        try:
            audio = await asyncio.to_thread(AudioSegment.from_file, out_file)
        finally:
            # pydub has the whole thing in memory now
            if os.path.exists(out_file):
                os.remove(out_file)

        view = MusicPlayerView(self, inter, current_song, len(audio))
        view.message = await inter.followup.send(embed=view.render(), view=view, wait=True)

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

        if len(music_queue[inter.guild.id]) > 0:
            await self.play_next(inter)

    @app_commands.command(name="skip", description="Skip the current song")
    async def skip(self, inter: discord.Interaction):
        await inter.response.defer()
        if not is_playing(inter, self.generate_music_identitiy(inter)):
            return await inter.followup.send('Nothing is playing')
        stop_user(inter, self.generate_music_identitiy(inter))
        await inter.followup.send("⏭  Skipped the current song")

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

        await inter.followup.send(embed=embed)

    @app_commands.command(name="stop", description="Stop the audio")
    async def stop(self, inter: discord.Interaction):
        await inter.response.defer()
        music_queue[inter.guild.id] = []
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
