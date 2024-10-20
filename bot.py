#!/usr/bin/python3
import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import os
import traceback

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)

if not discord.opus.is_loaded:
    discord.opus.load_opus()

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
    await bot.load_extension("commands.tts")
    await bot.load_extension("commands.music")
    await bot.load_extension("commands.hashiruCommands")

bot.run(TOKEN)
