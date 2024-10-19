import asyncio
import typing
from youtube_search import YoutubeSearch
from pytubefix import YouTube
import os
import discord
from discord import Embed, app_commands
from discord.ext import commands
from utils.audio_player import play, is_playing, stop_user, pause_user, resume_user
from pydub import AudioSegment
from discord.ui.select import BaseSelect

music_queue = {}

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
    def __init__(self, url):
        self.url = url
        self.yt = YouTube(url)


class MusicCommands(commands.Cog):
    async def autocomplete_link(self,  inter: discord.Interaction, current: str):
        string_option_input = current
        if not string_option_input or len(string_option_input) < 3:
            return []
        results = self.search_youtube(string_option_input)
        choices = []
        for result in results:
            choices.append(
                app_commands.Choice[str](name=result['title'],
                                         value='https://www.youtube.com'+result['url_suffix']))
        return choices
    
    @app_commands.command(name="play", description="play a song!")
    @app_commands.describe(
        link="The song to play"
    )
    @app_commands.autocomplete(link=autocomplete_link)
    async def play(self, inter: discord.Interaction, link: str):
        await inter.response.defer()

        # check if link is a youtube link
        if "youtube.com" not in link:
            link = self.search_youtube(link)[0]['url_suffix']

        music_queue[inter.guild.id] = music_queue.get(inter.guild.id, [])
        music_queue[inter.guild.id].append(MusicQueueSong(link))

        # add to queue if already playing
        if is_playing(inter, "test"):
            print("Playing next")
            return await inter.followup.send(f"Added {link} to the queue")

        await self.play_next(inter)

    async def play_next(self,  inter: discord.Interaction):
        current_song = music_queue[inter.guild.id].pop(0)

        # yt = YouTube(current_song)
        yt = current_song.yt

        # extract only audio
        video = yt.streams.filter(only_audio=True).first()
        out_file = video.download(output_path='.')

        # Get the audio using YTDL
        audio = AudioSegment.from_file(out_file)
        # create a player using embed
        embed = Embed(title="Now Playing", color=0x00ff00)
        embed.add_field(name="Title", value=yt.title, inline=False)
        embed.add_field(name="Duration", value=yt.length, inline=False)
        embed.set_thumbnail(url=yt.thumbnail_url)
        # add buttons to skip, pause, resume, stop
        view = BaseView()
        view.add_item(discord.ui.Button(style=discord.ButtonStyle.primary, label="Pause",
                                        emoji="⏸",
                                        custom_id="pause"))
        view.add_item(discord.ui.Button(style=discord.ButtonStyle.primary, label="Stop", 
                                        emoji="⏹",
                                        custom_id="stop"))
        view.add_item(discord.ui.Button(style=discord.ButtonStyle.primary, label="Skip", 
                                        emoji="⏭",
                                        custom_id="skip"))
        await inter.followup.send(embed=embed, view=view)

        await play(inter, audio, "test")
        while is_playing(inter, "test"):
            await asyncio.sleep(1)
        print("Playing next")
        # delete the file
        os.remove(out_file)
        if len(music_queue[inter.guild.id]) > 0:
            await self.play_next(inter)

    @app_commands.command(name="skip", description="Skip the current song")
    async def skip(self, inter: discord.Interaction):
        await inter.response.defer()
        await inter.followup.send("Skipping the current song")
        await self.skip_current(inter)

    async def skip_current(self, inter: discord.Interaction):
        await inter.response.defer()
        if inter.guild.id not in music_queue or len(music_queue[inter.guild.id]) == 0:
            return await inter.followup.send('No songs in queue')
        stop_user(inter, "test")

    def search_youtube(self, query):
        return YoutubeSearch(query, max_results=5).to_dict()

    @app_commands.command(name="queue", description="Show the current queue")
    async def queue(self, inter: discord.Interaction):
        await inter.response.defer()
        if len(music_queue[inter.guild.id]) == 0:
            return await inter.followup.send('No songs in queue')
        # create an embed with the current queue
        embed = Embed(title="Queue", color=0x00ff00)
        for i, song in enumerate(music_queue[inter.guild.id]):
            embed.add_field(name=f"{i+1}. {song.yt.title}",
                            value=song.yt.length, inline=False)
        await inter.followup.send(embed=embed)

    @app_commands.command(name="stop", description="Stop the audio")
    async def stop(self, inter: discord.Interaction):
        await inter.response.defer()
        await self.stop_audio(inter)

    async def stop_audio(self, inter: discord.Interaction):
        await inter.response.defer()
        stop_user(inter, "test")
        # clear the queue
        music_queue[inter.guild.id] = []
        await inter.followup.send('Stopped the audio')

    @app_commands.command(name="pause", description="Pause the audio")
    async def pause(self, inter: discord.Interaction):
        await inter.response.defer()
        await self.pause_audio(inter)
        await inter.followup.send('Paused the audio')

    async def pause_audio(self, inter: discord.Interaction):
        await inter.response.defer()
        pause_user(inter, "test")

    @app_commands.command(name="resume", description="Resume the audio")
    async def resume(self, inter: discord.Interaction):
        await inter.response.defer()
        await self.resume_audio(inter)
        await inter.followup.send('Resumed the audio')

    async def resume_audio(self, inter: discord.Interaction):
        await inter.response.defer()
        if not is_playing(inter, "test"):
            return await inter.followup.send('No audio to resume')
        resume_user(inter, "test")

    @app_commands.command(name="play_file", description="Play an audio file")
    @app_commands.describe(
        file="Upload file to play",
    )
    async def play_file(self, inter: discord.Interaction, file: discord.Attachment):
        await inter.response.defer()
        print("Playing "+file.url)
        audio = AudioSegment.from_file(file.url)
        await play(inter, audio, "test")
        await inter.followup.send("Playing file " + file.url)
    
    @commands.Cog.listener()
    async def on_interaction(self, inter: discord.Interaction):
        origin_msg = inter.message  
        if "custom_id" not in inter.data:
            return
        if inter.data["custom_id"] == "pause":
            await self.pause_audio(inter)
            view = BaseView()
            view.add_item(discord.ui.Button(style=discord.ButtonStyle.primary, label="Resume",
                                            emoji="▶️",
                                            custom_id="resume"))
            view.add_item(discord.ui.Button(style=discord.ButtonStyle.primary, label="Stop",
                                            emoji="⏹",
                                            custom_id="stop"))
            view.add_item(discord.ui.Button(style=discord.ButtonStyle.primary, label="Skip",
                                            emoji="⏭",
                                            custom_id="skip"))
            await origin_msg.edit(view=view)
        elif inter.data["custom_id"] == "resume":
            await self.resume_audio(inter)
            view = BaseView()
            view.add_item(discord.ui.Button(style=discord.ButtonStyle.primary, label="Pause",
                                            emoji="⏸",
                                            custom_id="pause"))
            view.add_item(discord.ui.Button(style=discord.ButtonStyle.primary, label="Stop",
                                            emoji="⏹",
                                            custom_id="stop"))
            view.add_item(discord.ui.Button(style=discord.ButtonStyle.primary, label="Skip",
                                            emoji="⏭",
                                            custom_id="skip"))
            await origin_msg.edit(view=view)
        elif inter.data["custom_id"] == "stop":
            await self.stop_audio(inter)
        elif inter.data["custom_id"] == "skip":
            await self.skip_current(inter)


async def setup(bot):
    print("Adding MusicCommands")
    await bot.add_cog(MusicCommands(bot))


async def teardown(bot):
    print("Unloaded MusicCommands")
