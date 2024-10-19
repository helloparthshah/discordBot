import os
import discord
from dotenv import load_dotenv
import pydub
import requests
from utils.audio_player import play, set_volume
from discord import File, app_commands
from discord.ext import commands

class TTSCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        load_dotenv()
        self.PLAY_HT_KEY = os.getenv('PLAY_HT_KEY')
        self.PLAY_HT_APP_ID = os.getenv('PLAY_HT_APP_ID')
        self.cloned_voices = []
        self.fetch_voices()

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

    @app_commands.command(name="update_voices", description="Update the list of voices")
    async def update_voices(self, inter: discord.Interaction):
        await inter.response.defer()
        self.fetch_voices()
        await inter.followup.send("Voices updated")
    
    async def autocomplete_voice(self, inter: discord.Interaction, current: str):
        choices = []
        for voice in self.cloned_voices:
             choices.append(
                 app_commands.Choice[str](
                name = voice['name'],
                value = voice['id']))
        return choices

    @app_commands.command(name="tts", description="Text to speech")
    @app_commands.describe(
        text = "String Option",
        voice = "The voice to use"
    )
    @app_commands.autocomplete(voice=autocomplete_voice)
    async def tts(self, inter: discord.Interaction, text: str, voice: str = "s3://voice-cloning-zero-shot/29652fa7-7a8c-4162-a69a-509d2b6bfc05/Harshil/manifest.json"):
        await inter.response.defer()
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
            await inter.followup.send(file=File('tts.mp3'))
            audio = pydub.AudioSegment.from_file('tts.mp3')
            await play(inter, audio, inter.user.id)
        else:
            await inter.followup.send("Error: "+str(response.status_code))


async def setup(bot):
    print("Adding TTSCommands")
    await bot.add_cog(TTSCommands(bot))


async def teardown(bot):
    print("Unloaded TTSCommands")
