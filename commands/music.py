from youtube_search import YoutubeSearch
from interactions.api.voice.audio import AudioVolume
from pytubefix import YouTube
from interactions import Extension, OptionType, slash_command, slash_option
from interactions import AutocompleteContext, SlashContext, Embed, listen
from interactions import Button, ButtonStyle, ActionRow
import os
import discord
from interactions.api.events import Component

music_queue = {}


class MusicQueueSong:
    def __init__(self, url):
        self.url = url
        self.yt = YouTube(url, client='WEB_CREATOR')


class MusicCommands(Extension):
    @slash_command(name="play", description="play a song!")
    @slash_option(
        name="link",
        description="The song to play",
        required=True,
        opt_type=OptionType.STRING,
        autocomplete=True
    )
    async def play(self, ctx: SlashContext, *, link: str):
        await ctx.defer()
        if not ctx.voice_state:
            await ctx.author.voice.channel.connect()

        # check if link is a youtube link
        if "youtube.com" not in link:
            link = self.search_youtube(link)[0]['url_suffix']

        music_queue[ctx.guild_id] = music_queue.get(ctx.guild_id, [])
        music_queue[ctx.guild_id].append(MusicQueueSong(link))

        # add to queue if already playing
        if ctx.voice_state.playing:
            return await ctx.send(f"Added {link} to the queue")

        await self.play_next(ctx)

    async def play_next(self, ctx: SlashContext):
        current_song = music_queue[ctx.guild_id].pop(0)

        # yt = YouTube(current_song)
        yt = current_song.yt

        # extract only audio
        video = yt.streams.filter(only_audio=True).first()
        out_file = video.download(output_path='.')

        # Get the audio using YTDL
        audio = AudioVolume(out_file)
        # create a player using embed
        embed = Embed(title="Now Playing", color=0x00ff00)
        embed.add_field(name="Title", value=yt.title, inline=False)
        embed.add_field(name="Duration", value=yt.length, inline=False)
        embed.set_thumbnail(url=yt.thumbnail_url)
        # add buttons to skip, pause, resume, stop
        await ctx.send(embed=embed, components=[
            ActionRow(
                Button(
                    style=ButtonStyle.GREY,
                    emoji="⏸",
                    custom_id="pause"
                ),
                Button(
                    style=ButtonStyle.GREY,
                    emoji="⏹",
                    custom_id="stop"
                ),
                Button(
                    style=ButtonStyle.GREY,
                    emoji="⏭",
                    custom_id="skip"
                ),
            )
        ])

        await ctx.voice_state.play(audio)
        # delete the file
        os.remove(out_file)
        if len(music_queue[ctx.guild_id]) > 0:
            await self.play_next(ctx)

    @slash_command(name="skip", description="Skip the current song")
    async def skip(self, ctx: SlashContext):
        await ctx.send("Skipping the current song")
        await self.skip_current(ctx)

    async def skip_current(self, ctx: SlashContext):
        if not ctx.voice_state:
            return await ctx.send('Not connected to any voice channel')
        if len(music_queue[ctx.guild_id]) == 0:
            return await ctx.send('No songs in queue')
        await ctx.voice_state.stop()

    @play.autocomplete("link")
    async def autocomplete(self, ctx: AutocompleteContext):
        string_option_input = ctx.input_text
        if not string_option_input or len(string_option_input) < 3:
            return await ctx.send(choices=[])
        results = self.search_youtube(string_option_input)
        choices = []
        for result in results:
            choices.append({
                "name": result['title'],
                "value": 'https://www.youtube.com'+result['url_suffix']
            })
        await ctx.send(
            choices=choices
        )

    def search_youtube(self, query):
        return YoutubeSearch(query, max_results=5).to_dict()

    @slash_command(name="queue", description="Show the current queue")
    async def queue(self, ctx: SlashContext):
        if len(music_queue[ctx.guild_id]) == 0:
            return await ctx.send('No songs in queue')
        # create an embed with the current queue
        embed = Embed(title="Queue", color=0x00ff00)
        for i, song in enumerate(music_queue[ctx.guild_id]):
            embed.add_field(name=f"{i+1}. {song.yt.title}",
                            value=song.yt.length, inline=False)
        await ctx.send(embed=embed)

    @slash_command(name="stop", description="Stop the audio")
    async def stop(self, ctx=SlashContext):
        await ctx.defer()
        await self.stop_audio(ctx)

    async def stop_audio(self, ctx: SlashContext):
        if not ctx.voice_state:
            return await ctx.send('Not connected to any voice channel')
        await ctx.voice_state.stop()
        # clear the queue
        music_queue[ctx.guild_id] = []
        await ctx.send('Stopped the audio')

    @slash_command(name="pause", description="Pause the audio")
    async def pause(self, ctx=SlashContext):
        await ctx.defer()
        await self.pause_audio(ctx)
        await ctx.send('Paused the audio')

    async def pause_audio(ctx: SlashContext):
        if not ctx.voice_state:
            return await ctx.send('Not connected to any voice channel')
        ctx.voice_state.pause()

    @slash_command(name="resume", description="Resume the audio")
    async def resume(self, ctx=SlashContext):
        await ctx.defer()
        await self.resume_audio(ctx)
        await ctx.send('Resumed the audio')

    async def resume_audio(self, ctx: SlashContext):
        if not ctx.voice_state:
            return await ctx.send('Not connected to any voice channel')
        ctx.voice_state.resume()

    @slash_command(name="volume", description="Change the volume")
    @slash_option(
        name="volume",
        description="The volume to set",
        required=True,
        opt_type=OptionType.INTEGER
    )
    async def volume(self, ctx=SlashContext, *, volume: int):
        await ctx.defer()
        if not ctx.voice_state:
            return await ctx.send('Not connected to any voice channel')
        ctx.voice_state.volume = volume / 100
        await ctx.send(f"Volume set to {volume}%")

    @slash_command(name="play_file", description="Play an audio file")
    @slash_option(
        name="file",
        description="Upload file to play",
        required=True,
        opt_type=OptionType.ATTACHMENT
    )
    async def play_file(self, ctx=SlashContext, *, file: discord.Attachment):
        await ctx.defer()
        if (not ctx.author.voice):
            return await ctx.channel.send('Join a channel first')
        if not ctx.voice_state:
            await ctx.author.voice.channel.connect()
        else:
            await ctx.voice_state.move(ctx.author.voice.channel)
        print("Playing "+file.url)
        audio = AudioVolume(file.url)
        await ctx.send("Playing "+file.url)
        await ctx.voice_state.play(audio)

    @listen(Component)
    async def on_component(self, event: Component):
        ctx = event.ctx
        if ctx.custom_id == "pause":
            # change icon and label to resume
            await self.pause_audio(ctx)
            await ctx.edit_origin(components=[
                ActionRow(
                    Button(
                        style=ButtonStyle.green,
                        emoji="▶️",
                        custom_id="resume"
                    ),
                    Button(
                        style=ButtonStyle.grey,
                        emoji="⏹",
                        custom_id="stop"
                    ),
                    Button(
                        style=ButtonStyle.grey,
                        emoji="⏭",
                        custom_id="skip"
                    ),
                )
            ])
        elif ctx.custom_id == "resume":
            await self.resume_audio(ctx)
            await ctx.edit_origin(components=[
                ActionRow(
                    Button(
                        style=ButtonStyle.grey,
                        emoji="⏸",
                        custom_id="pause"
                    ),
                    Button(
                        style=ButtonStyle.grey,
                        emoji="⏹",
                        custom_id="stop"
                    ),
                    Button(
                        style=ButtonStyle.grey,
                        emoji="⏭",
                        custom_id="skip"
                    ),
                )
            ])
        elif ctx.custom_id == "stop":
            return await self.stop_audio(ctx)
        elif ctx.custom_id == "skip":
            return await self.skip_current(ctx)


def setup(bot):
    MusicCommands(bot)
