from interactions import AutocompleteContext, Extension, File, OptionType, slash_command, slash_option
import os
from dotenv import load_dotenv
from interactions import SlashContext
import requests
from interactions.api.voice.audio import AudioVolume


class TTSCommands(Extension):
    def __init__(self, bot):
        load_dotenv()
        self.PLAY_HT_KEY = os.getenv('PLAY_HT_KEY')
        self.PLAY_HT_APP_ID = os.getenv('PLAY_HT_APP_ID')
        self.cloned_voices = []

    async def async_start(self):
        self.fetch_voices()

    def fetch_voices(self):
        print("Fetching voices")
        url = "https://api.play.ht/api/v2/cloned-voices"
        headers = {
            "AUTHORIZATION": self.PLAY_HT_KEY,
            "X-USER-ID": self.PLAY_HT_APP_ID
        }
        response = requests.get(url, headers=headers)
        self.cloned_voices = response.json()

    @slash_command(name="update_voices", description="Update the list of voices")
    async def update_voices(self, ctx: SlashContext):
        await ctx.defer()
        self.fetch_voices()
        await ctx.send("Voices updated")

    @slash_command(name="tts", description="Text to speech")
    @slash_option(
        name="text",
        description="String Option",
        required=True,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="voice",
        description="The voice to use",
        required=False,
        opt_type=OptionType.STRING,
        autocomplete=True
    )
    async def tts(self, ctx=SlashContext, *, text: str, voice: str = "s3://voice-cloning-zero-shot/29652fa7-7a8c-4162-a69a-509d2b6bfc05/Harshil/manifest.json"):
        await ctx.defer()
        url = "https://api.play.ht/api/v2/tts/stream"
        payload = {
            "text": text,
            "voice": voice,
            "output_format": "mp3",
            "voice_engine": "PlayHT2.0"
        }
        headers = {
            "accept": "audio/mpeg",
            "content-type": "application/json",
            "AUTHORIZATION": self.PLAY_HT_KEY,
            "X-USER-ID": self.PLAY_HT_APP_ID
        }
        print("Connecting to voice channel")
        # if not joined a voice channel, join the user's voice channel

        print("Sending request")
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            # save the audio file
            with open('tts.mp3', 'wb') as f:
                f.write(response.content)
            audio = AudioVolume('tts.mp3')
            await ctx.send(file=File('tts.mp3'))
            if (not ctx.author.voice):
                return
            if ctx.guild_id == ctx.author.voice.channel.guild.id:
                if not ctx.voice_state:
                    await ctx.author.voice.channel.connect()
                else:
                    await ctx.voice_state.move(ctx.author.voice.channel)
                await ctx.voice_state.play(audio)
        else:
            await ctx.send("Error: "+str(response.status_code))

    @tts.autocomplete("voice")
    async def autocomplete(self, ctx: AutocompleteContext):
        string_option_input = ctx.input_text  # can be empty/None
        # you can use ctx.kwargs.get("name") to get the current state of other options - note they can be empty too

        # make sure you respond within three seconds
        choices = []
        for voice in self.cloned_voices:
            choices.append({
                "name": voice['name'],
                "value": voice['id']
            })
        await ctx.send(
            choices=choices
        )


def setup(bot):
    TTSCommands(bot)
