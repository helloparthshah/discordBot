import typing
import discord
from discord import app_commands
from discord.ui.select import BaseSelect
from discord.ext import commands
import os
from dotenv import load_dotenv
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
import requests
import pydub

import utils
import utils.audio_player
from utils.audio_player import play, set_volume


class BaseView(discord.ui.View):
    interaction: discord.Interaction | None = None
    message: discord.Message | None = None

    def __init__(self, timeout: float = None):
        super().__init__(timeout=timeout)

    # make sure that the view only processes interactions from the user who invoked the command
    # async def interaction_check(self, interaction: discord.Interaction) -> bool:
    #     if interaction.user.id != self.user.id:
    #         await interaction.response.send_message(
    #             "You cannot interact with this view.", ephemeral=True
    #         )
    #         return False
    #     # update the interaction attribute when a valid interaction is received
    #     self.interaction = interaction
    #     return True

    # to handle errors we first notify the user that an error has occurred and then disable all components

    def _disable_all(self) -> None:
        # disable all components
        # so components that can be disabled are buttons and select menus
        for item in self.children:
            if isinstance(item, discord.ui.Button) or isinstance(item, BaseSelect):
                item.disabled = True

    # after disabling all components we need to edit the message with the new view
    # now when editing the message there are two scenarios:
    # 1. the view was never interacted with i.e in case of plain timeout here message attribute will come in handy
    # 2. the view was interacted with and the interaction was processed and we have the latest interaction stored in the interaction attribute
    async def _edit(self, **kwargs: typing.Any) -> None:
        if self.interaction is None and self.message is not None:
            # if the view was never interacted with and the message attribute is not None, edit the message
            await self.message.edit(**kwargs)
        elif self.interaction is not None:
            try:
                # if not already responded to, respond to the interaction
                await self.interaction.response.edit_message(**kwargs)
            except discord.InteractionResponded:
                # if already responded to, edit the response
                await self.interaction.edit_original_response(**kwargs)

    async def on_timeout(self) -> None:
        # disable all components
        self._disable_all()
        # edit the message with the new view
        await self._edit(view=self)


class SoundboardCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        load_dotenv()
        MONGODB_DB_URL = os.getenv('MONGODB_DB_URL')

        print("Loading sounds")
        MongoDbClient = MongoClient(MONGODB_DB_URL, server_api=ServerApi('1'))
        database = MongoDbClient["discord-bot"]
        self.soundboardCollection = database["soundboard"]

    def saveFile(self, url, id):
        if not os.path.exists("sounds"):
            os.makedirs("sounds")
        content = requests.get(url).content
        ext = url.split(".")[-1].split("?")[0]
        filename = "sounds/"+id+"."+ext
        # check if file exists in cache (/sounds folder)
        if not os.path.exists(filename):
            with open(filename, "wb") as f:
                f.write(content)
        return filename

    def writeRawFile(self, filename, content):
        if not os.path.exists("sounds"):
            os.makedirs("sounds")
        if not os.path.exists(filename):
            with open(filename, "wb") as f:
                f.write(content)
        return filename

    async def playUrl(self, inter: discord.Interaction, id):
        print("received sound request")
        filename = ""
        if not os.path.exists("sounds"):
            os.makedirs("sounds")
        for file in os.listdir("sounds"):
            if file.startswith(id):
                print("Sound found in cache")
                filename = "sounds/"+file
        # check if file with id exists in cache
        if filename == "":
            print("Sound not found in cache")
            # get sound from database
            sound = self.soundboardCollection.find_one({"_id": id})
            url = sound['sound']
            print("Playing sound "+sound['name'])
            # save the file to cache
            ext = url.split(".")[-1].split("?")[0]
            filename = "sounds/"+id+"."+ext
            if 'raw_sound' in sound:
                self.writeRawFile(filename, sound['raw_sound'])
            else:
                filename = self.saveFile(url, id)
        # join the voice channel and play the audio

        await play(inter, pydub.AudioSegment.from_file(filename), inter.user.id)
        print("finished sending sound")

    @app_commands.command(name="add_sound", description="Add a sound to the soundboard")
    @app_commands.describe(name="The name of the sound",
                           emoji="The emoji to use for the sound",
                           sound="The sound to add")
    async def add_sound(self, inter: discord.Interaction, name: str, emoji: str, sound: discord.Attachment):
        if not inter.user.guild_permissions.create_expressions:
            await inter.response.send_message("You do not have permission to add sounds")
            return
        await inter.response.defer()
        print("Adding sound "+name)
        print(sound.url)
        soundId = name.lower()+"_"+str(inter.guild_id)

        url = sound.url
        content = requests.get(url).content
        soundboardRow = {"$set":
                         {
                             "_id": soundId,
                             "name": name,
                             "server": inter.guild_id,
                             "emoji": emoji,
                             "sound": url,
                             "raw_sound": content
                         }}
        self.soundboardCollection.update_one(
            {"_id": soundId}, soundboardRow, upsert=True)
        await inter.followup.send("Added sound "+name)

    @app_commands.command(name="add_sound_url", description="Add a sound to the soundboard")
    @app_commands.describe(name="The name of the sound", emoji="The emoji to use for the sound", url="The sound to add")
    async def add_sound_url(self, inter: discord.Interaction, name: str, emoji: str, url: str):
        if not inter.user.guild_permissions.create_expressions:
            await inter.response.send_message("You do not have permission to add sounds")
            return
        await inter.response.defer()
        soundId = name.lower()+"_"+str(inter.guild_id)

        soundboardRow = {"$set":
                         {
                             "_id": soundId,
                             "name": name,
                             "server": inter.guild_id,
                             "emoji": emoji,
                             "sound": url,
                         }}
        self.soundboardCollection.update_one(
            {"_id": soundId}, soundboardRow, upsert=True)
        await inter.response.send_message("Added sound "+name)

    async def autocomplete_name(self, inter: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        sounds = self.soundboardCollection.find(
            {"server": inter.guild_id, "name": {"$regex": current, "$options": "i"}})
        choices = []
        # max 25 choices
        for sound in sounds.limit(25):
            choices.append(
                app_commands.Choice[str](name=sound["name"], value=sound["name"]))
        return choices

    async def autocomplete_emoji(self, inter: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        print(inter.namespace)
        sound = self.soundboardCollection.find_one(
            {"server": inter.guild_id, "name": inter.namespace.name})
        return [app_commands.Choice[str](name=sound["emoji"], value=sound["emoji"])]

    @app_commands.command(name="update_sound_url", description="Update a sound on the soundboard")
    @app_commands.describe(name="The name of the sound to update", emoji="The emoji to use for the sound", url="Link to the sound")
    @app_commands.autocomplete(name=autocomplete_name)
    @app_commands.autocomplete(emoji=autocomplete_emoji)
    async def update_sound_url(self, inter: discord.Interaction, name: str, emoji: str, url: str):
        if not inter.user.guild_permissions.create_expressions:
            await inter.response.send_message("You do not have permission to update sounds")
            return
        await inter.response.defer()
        soundId = name.lower()+"_"+str(inter.guild_id)
        # update the sound in the database but remove the raw_sound field
        soundboardRow = {"$set":
                         {
                             "_id": soundId,
                             "name": name,
                             "server": inter.guild_id,
                             "emoji": emoji,
                             "sound": url,
                         },
                         "$unset": {"raw_sound": ""}}

        self.soundboardCollection.update_one(
            {"_id": soundId}, soundboardRow)
        # delete the sound file from cache
        ext = url.split(".")[-1].split("?")[0]
        filename = "sounds/"+soundId+"."+ext
        if os.path.exists(filename):
            os.remove(filename)
        await inter.followup.send("Updated sound "+name)

    @app_commands.command(name="update_sound", description="Update a sound on the soundboard")
    @app_commands.describe(name="The name of the sound to update", emoji="The emoji to use for the sound", sound="The sound to add")
    @app_commands.autocomplete(name=autocomplete_name)
    @app_commands.autocomplete(emoji=autocomplete_emoji)
    async def update_sound(self, inter: discord.Interaction, name: str, emoji: str, sound: discord.Attachment):
        if not inter.user.guild_permissions.create_expressions:
            await inter.response.send_message("You do not have permission to update sounds")
            return
        await inter.response.defer()
        print("Updating sound "+name)
        print(sound.url)
        soundId = name.lower()+"_"+str(inter.guild_id)

        url = sound.url
        content = requests.get(url).content
        soundboardRow = {"$set":
                         {
                             "_id": soundId,
                             "name": name,
                             "server": inter.guild_id,
                             "emoji": emoji,
                             "sound": url,
                             "raw_sound": content
                         }}
        self.soundboardCollection.update_one(
            {"_id": soundId}, soundboardRow, upsert=True)
        # delete the sound file from cache
        ext = url.split(".")[-1].split("?")[0]
        filename = "sounds/"+soundId+"."+ext
        if os.path.exists(filename):
            os.remove(filename)
        await inter.followup.send("Updated sound "+name)

    @app_commands.command(name="remove_sound", description="Remove a sound from the soundboard")
    @app_commands.describe(name="The name of the sound to remove")
    @app_commands.autocomplete(name=autocomplete_name)
    async def remove_sound(self, inter: discord.Interaction, name: str):
        if not inter.user.guild_permissions.create_expressions:
            await inter.response.send_message("You do not have permission to remove sounds")
            return
        await inter.response.defer()
        soundId = name.lower()+"_"+str(inter.guild_id)
        self.soundboardCollection.delete_one({"_id": soundId})
        await inter.followup.send("Removed sound "+name)

    async def playSoundCallback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if interaction.data["custom_id"].startswith("soundboard_sound_"):
            id = interaction.data["custom_id"].replace("soundboard_sound_", "")
            await self.playUrl(interaction, id)

    @app_commands.command(name="soundboard", description="Play a sound from the soundboard")
    async def soundboard(self, inter: discord.Interaction):
        # get all sounds for the server
        sounds = self.soundboardCollection.find(
            {"server": inter.guild_id})
        buttons = []
        for sound in sounds:
            # add buttons for each sound
            button = discord.ui.Button(
                style=discord.ButtonStyle.primary,
                label=sound['name'],
                emoji=sound['emoji'],
                custom_id="soundboard_sound_"+sound['_id']
            )
            buttons.append(button)
        if (len(buttons) == 0):
            await inter.response.send_message("No sounds available")
            return
        embed = discord.Embed(title="Soundboard", color=0x00ff00,
                              description="Choose a sound to play")
        # can only have 25 buttons per message
        buttonGroups = [buttons[i:i + 25] for i in range(0, len(buttons), 25)]

        for buttonGroup in buttonGroups:
            view = BaseView()
            for button in buttonGroup:
                view.add_item(button)
            if inter.response.is_done():
                await inter.followup.send(view=view)
            else:
                await inter.response.send_message(view=view)

    @app_commands.command(name="volume", description="Set volume of music player")
    async def update_volume(self, inter: discord.Interaction, volume: int):
        if volume < 1 or volume > 100:
            await inter.response.send_message("\U0000274C Volume must be between 1 and 100")
            return
        self.set_soundboard_volume(inter, volume)
        await inter.response.send_message(f"Set soundboard volume to {volume}")

    def set_soundboard_volume(self, inter: discord.Interaction, volume: int):
        set_volume(inter, volume)
        
    @commands.Cog.listener()
    async def on_interaction(self, inter: discord.Interaction):
        if "custom_id" not in inter.data:
            return
        if inter.data["custom_id"].startswith("soundboard_sound_"):
            await self.playSoundCallback(inter)


async def setup(bot):
    print("Adding soundboard")
    await bot.add_cog(SoundboardCommands(bot))


async def teardown(bot):
    print("Unloaded Soundboard")
