import discord
from interactions import Button, ButtonStyle, Embed, Extension, OptionType, Permissions, slash_command, slash_option, spread_to_rows, listen
import os
from dotenv import load_dotenv
from interactions import SlashContext
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
import requests
from interactions.api.events import Component
from interactions.api.voice.audio import AudioVolume


class SoundboardCommands(Extension):
    def __init__(self, bot):
        load_dotenv()
        MONGODB_DB_URL = os.getenv('MONGODB_DB_URL')

        MongoDbClient = MongoClient(MONGODB_DB_URL, server_api=ServerApi('1'))
        database = MongoDbClient["discord-bot"]
        self.soundboardCollection = database["soundboard"]

    async def playUrl(self, ctx, id):
        sound = self.soundboardCollection.find_one({"_id": id})
        url = sound['sound']
        print("Playing sound "+sound['name'])
        if not os.path.exists("sounds"):
            os.makedirs("sounds")
        # check if file exists in cache (/sounds folder)
        if not os.path.exists("sounds/"+id+".mp3"):
            content = requests.get(url).content
            with open("sounds/"+id+".mp3", "wb") as f:
                f.write(content)
        # join the voice channel and play the audio
        if not ctx.voice_state:
            await ctx.author.voice.channel.connect()
        else:
            await ctx.voice_state.move(ctx.author.voice.channel)
        audio = AudioVolume("sounds/"+id+".mp3")
        await ctx.voice_state.play(audio)

    @slash_command(name="add_sound", description="Add a sound to the soundboard")
    @slash_option(
        name="name",
        description="The name of the sound",
        opt_type=OptionType.STRING,
        required=True
    )
    @slash_option(
        name="emoji",
        description="The emoji to use for the sound",
        opt_type=OptionType.STRING,
        required=True
    )
    @slash_option(
        name="sound",
        description="The sound to add",
        opt_type=OptionType.ATTACHMENT,
        required=True
    )
    async def add_sound(self, ctx=SlashContext, *, name: str, emoji: str, sound: discord.Attachment):
        if not ctx.author.has_permission(Permissions.CREATE_GUILD_EXPRESSIONS):
            await ctx.send("You do not have permission to add sounds")
            return
        await ctx.defer()
        print("Adding sound "+name)
        print(sound.url)
        soundId = name.lower()+"_"+str(ctx.guild_id)
        soundboardRow = {"$set":
                         {
                             "_id": soundId,
                             "name": name,
                             "server": ctx.guild_id,
                             "emoji": emoji,
                             "sound": sound.url
                         }}
        self.soundboardCollection.update_one(
            {"_id": soundId}, soundboardRow, upsert=True)
        await ctx.send("Added sound "+name)
    
    # @slash_command(name="update_sound", description="Update a sound on the soundboard")
    # @slash_option(
    #     name="name",
    #     description="The name of the sound to update",
    #     opt_type=OptionType.STRING,
    #     required=True
    # )
    # @slash_option(
    #     name="emoji",
    #     description="The emoji to use for the sound",
    #     opt_type=OptionType.STRING,
    #     required=True
    # )
    # @slash_option(
    #     name="sound",
    #     description="The sound to update",
    #     opt_type=OptionType.ATTACHMENT,
    #     required=True
    # )
    # async def update_sound(self, ctx=SlashContext, *, name: str, emoji: str, sound: discord.Attachment):
        

    @slash_command(name="remove_sound", description="Remove a sound from the soundboard")
    @slash_option(
        name="name",
        description="The name of the sound to remove",
        opt_type=OptionType.STRING,
        required=True
    )
    async def remove_sound(self, ctx=SlashContext, *, name: str):
        if not ctx.author.has_permission(Permissions.CREATE_GUILD_EXPRESSIONS):
            await ctx.send("You do not have permission to remove sounds")
            return
        await ctx.defer()
        soundId = name.lower()+"_"+str(ctx.guild_id)
        self.soundboardCollection.delete_one({"_id": soundId})
        await ctx.send("Removed sound "+name)

    @slash_command(name="soundboard", description="Play a sound from the soundboard")
    async def soundboard(self, ctx=SlashContext, *, name: str = None):
        # get all sounds for the server
        sounds = self.soundboardCollection.find(
            {"server": ctx.guild_id})
        buttons = []
        for sound in sounds:
            print(sound)
            # add buttons for each sound
            buttons.append(Button(
                style=ButtonStyle.PRIMARY,
                label=sound['name'],
                emoji=sound['emoji'],
                custom_id="soundboard_sound_"+sound['_id']
            ))
        if (len(buttons) == 0):
            await ctx.send("No sounds available")
            return
        embed = Embed(title="Soundboard", color=0x00ff00,
                      description="Choose a sound to play")
        await ctx.send(embed=embed, components=spread_to_rows(*buttons))

    @listen(Component)
    async def on_component(self, event: Component):
        ctx = event.ctx
        if ctx.custom_id.startswith("soundboard_sound_"):
            id = ctx.custom_id.replace("soundboard_sound_", "")
            await ctx.edit_origin(content="")
            await self.playUrl(ctx, id)


def setup(bot):
    SoundboardCommands(bot)
