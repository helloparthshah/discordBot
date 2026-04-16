#!/usr/bin/python3
import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import os
import traceback
import random

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
USER_ID_THUMBS_DOWN = 375859366395641858
USER_ID_CHUS = 347605620012351488
CHUS_REACTION_RESPONSES = [
    "CHUS",
    "MUTH",
    "CHUTIYA",
    "67",
    "HARSHIL",
    "APT. 184",
]

KEYCAP_DIGIT_EMOJI = {
    "0": "0️⃣",
    "1": "1️⃣",
    "2": "2️⃣",
    "3": "3️⃣",
    "4": "4️⃣",
    "5": "5️⃣",
    "6": "6️⃣",
    "7": "7️⃣",
    "8": "8️⃣",
    "9": "9️⃣",
}

REGIONAL_INDICATOR_A = ord("🇦")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)

if not discord.opus.is_loaded:
    discord.opus.load_opus()


def to_reaction_emojis(text: str) -> list[str]:
    emojis = []
    for char in text.upper():
        if "A" <= char <= "Z":
            emojis.append(chr(REGIONAL_INDICATOR_A + (ord(char) - ord("A"))))
        elif char in KEYCAP_DIGIT_EMOJI:
            emojis.append(KEYCAP_DIGIT_EMOJI[char])
        elif char == ".":
            emojis.append("▪️")
    return emojis

@bot.event
async def on_ready() -> None:
    print("Syncing command list")
    await bot.tree.sync()
    print("Bot is ready!")
    await bot.change_presence(activity=discord.Game(name=f"/help"))


@bot.tree.command(name="help", description="View all of the commands")
async def help(interaction: discord.Interaction):
    # dynamically create the embed
    embed = discord.Embed(title="Help", color=0x00ff00)
    for command in bot.tree.get_commands():
        embed.add_field(name="/"+str(command.name),
                        value=str(command.description), inline=False)
    await interaction.response.send_message(embed=embed)
    


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    traceback.print_exception(error)
    if not ctx.responded:
        await ctx.send_message("Something went wrong.")


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return

    if message.author.id == USER_ID_THUMBS_DOWN:
        await message.add_reaction("👎")
    elif message.author.id == USER_ID_CHUS:
        chosen_response = random.choice(CHUS_REACTION_RESPONSES)
        for emoji in to_reaction_emojis(chosen_response):
            await message.add_reaction(emoji)

    await bot.process_commands(message)


# @listen()
# async def on_message_create(event):
#     mention = str(bot.user.id)
#     if mention in event.message.content:
#         embed = interactions.Embed(
#             title="Thank you for using my bot",
#             description=f"I am a bot created by {bot.owner}",)
#         await event.message.channel.send(embed=embed)


@bot.event
async def setup_hook():
    print("Setting Up...")
    # extension_names = [m.name for m in pkgutil.iter_modules(
    #     ["commands"], prefix="commands.")]
    # for extension in extension_names:
    #     await bot.load_extension(extension)
    await bot.load_extension("commands.soundboard")
    await bot.load_extension("commands.extras")
    # await bot.load_extension("commands.tts")
    await bot.load_extension("commands.music")
    await bot.load_extension("commands.hashiruCommands")
    # await bot.load_extension("commands.llm")
    await bot.load_extension("commands.imageUtils")
    await bot.load_extension("commands.voiceUtils")

bot.run(TOKEN)
