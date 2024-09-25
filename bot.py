#!/usr/bin/python3
from interactions import Intents, listen, slash_command
import interactions
import os
import discord
from dotenv import load_dotenv
from interactions import SlashContext
import os
import traceback
from interactions.api.events import CommandError
import pkgutil

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

bot = interactions.Client(
    intents=Intents.DEFAULT | Intents.MESSAGE_CONTENT,
    sync_interactions=True,
)


@slash_command(name="help", description="View all of the commands")
async def help(ctx=SlashContext):
    # dynamically create the embed
    embed = interactions.Embed(title="Help", color=0x00ff00)
    for command in bot.application_commands:
        embed.add_field(name="/"+str(command.name),
                        value=str(command.description), inline=False)
    await ctx.send(embed=embed)


@listen(CommandError, disable_default_listeners=True)
async def on_command_error(event: CommandError):
    traceback.print_exception(event.error)
    if not event.ctx.responded:
        await event.ctx.send("Something went wrong.")


@listen()
async def on_message_create(event):
    mention = str(bot.user.id)
    if mention in event.message.content:
        embed = interactions.Embed(
            title="Thank you for using my bot",
            description=f"I am a bot created by {bot.owner}",)
        await event.message.channel.send(embed=embed)


@listen()
async def on_startup():
    print("Bot is ready!")
    await bot.change_presence(activity=discord.Game(name=f"/help"))

extension_names = [m.name for m in pkgutil.iter_modules(
    ["commands"], prefix="commands.")]
for extension in extension_names:
    bot.load_extension(extension)

bot.start(TOKEN)
