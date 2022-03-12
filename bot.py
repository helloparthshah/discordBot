#!/usr/bin/python3
from unicodedata import name
from googleapiclient.discovery import build
import os

import discord
from dotenv import load_dotenv
from discord import Client, Intents, Embed
from discord import FFmpegPCMAudio
from youtube_dl import YoutubeDL
import ctypes
import ctypes.util
import asyncio
from discord_slash import SlashCommand, SlashContext

print("ctypes - Find opus:")
a = ctypes.util.find_library('opus')
print(a)

print("Discord - Load Opus:")
b = discord.opus.load_opus(a)
print(b)

print("Discord - Is loaded:")
c = discord.opus.is_loaded()
print(c)

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
YT_KEY = os.getenv('YT_KEY')
global_volume = 1.0

bot = Client(intents=Intents.default())
slash = SlashCommand(bot, sync_commands=True)
_queue = []

YDL_OPTIONS = {'format': 'bestaudio', 'noplaylist': 'True'}
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', 'options': '-vn'}


@slash.slash(name="play")
async def play(ctx=SlashContext, *, query=None):
    if not query and ctx.voice_client.is_paused():
        return ctx.voice_client.resume()
    elif not query:
        return await ctx.send("No song is currently playing")

    if(not ctx.author.voice):
        return await ctx.send('Join a channel first')

    # voice = get(bot.voice_clients, guild=ctx.guild)
    channel = ctx.author.voice.channel

    if(not ctx.voice_client):
        voice = await channel.connect()
    else:
        voice = ctx.voice_client
        # voice.stop()

    youtube = build("youtube", "v3", developerKey=YT_KEY)
    search_response = youtube.search().list(
        q=query, part="id,snippet", maxResults=1).execute()
    vid = search_response['items'][0]['id']['videoId']
    # It will send the data in a .json format.
    video_link = 'https://www.youtube.com/watch?v=' + vid

    if(voice.is_playing()):
        _queue.append(video_link)
        print(_queue)
        await ctx.send(video_link)
        return

    with YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(video_link, download=False)
        URL = info['formats'][0]['url']
        voice.play(FFmpegPCMAudio(URL, **FFMPEG_OPTIONS),
                   after=lambda e: play_next(ctx))
        voice.is_playing()

        global global_volume
        print(global_volume)
        # voice.source.volume = 1
        voice.source = discord.PCMVolumeTransformer(
            voice.source, volume=global_volume)

    await ctx.send(video_link)


def play_next(ctx=SlashContext):
    voice = ctx.voice_client
    if(len(_queue) >= 1):
        info = YoutubeDL(YDL_OPTIONS).extract_info(
            _queue.pop(0), download=False)
        URL = info['formats'][0]['url']
        voice.play(FFmpegPCMAudio(URL, **FFMPEG_OPTIONS),
                   after=lambda e: play_next(ctx))
        global global_volume
        print(global_volume)
        # voice.source.volume = 1
        voice.source = discord.PCMVolumeTransformer(
            voice.source, volume=global_volume)
        ctx.send(_queue[0])
    else:
        # await asyncio.sleep(90)  # wait 1 minute and 30 seconds
        if not voice.is_playing():
            asyncio.run_coroutine_threadsafe(
                ctx.voice_client.disconnect(), bot.loop)
            asyncio.run_coroutine_threadsafe(
                ctx.send("No more songs in queue."), bot.loop)


@slash.slash(name="next")
async def next(ctx=SlashContext):
    if(not ctx.author.voice):
        return await ctx.send('Join a channel first')

    if(len(_queue) == 0):
        return await ctx.send('No songs in queue')

    # voice = get(bot.voice_clients, guild=ctx.guild)
    channel = ctx.author.voice.channel

    if(not ctx.voice_client):
        voice = await channel.connect()
    else:
        voice = ctx.voice_client
        voice.stop()

    with YoutubeDL(YDL_OPTIONS) as ydl:
        await ctx.send(_queue[0])
        info = ydl.extract_info(_queue.pop(), download=False)
        URL = info['formats'][0]['url']
        voice.play(FFmpegPCMAudio(URL, **FFMPEG_OPTIONS))
        voice.is_playing()

        global global_volume
        print(global_volume)
        voice.source.volume = 1
        voice.source = discord.PCMVolumeTransformer(
            voice.source, volume=global_volume)

    print(_queue)

@slash.slash(name="clear")
async def clear(ctx=SlashContext):
    _queue.clear()
    await ctx.send('Cleard queue')


@slash.slash(name="link")
async def link(ctx=SlashContext, *, query):
    if not query:
        return await ctx.send("No link provided")

    if(not ctx.author.voice):
        return await ctx.send('Join a channel first')

    # voice = get(bot.voice_clients, guild=ctx.guild)
    channel = ctx.author.voice.channel

    if(not ctx.voice_client):
        voice = await channel.connect()
    else:
        voice = ctx.voice_client
        # voice.stop()

    video_link = query

    if(voice.is_playing()):
        _queue.append(video_link)
        print(_queue)
        await ctx.send(video_link)
        return

    with YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(video_link, download=False)
        URL = info['formats'][0]['url']
        voice.play(FFmpegPCMAudio(URL, **FFMPEG_OPTIONS),
                   after=lambda e: play_next(ctx))
        voice.is_playing()

        global global_volume
        print(global_volume)
        # voice.source.volume = 1
        voice.source = discord.PCMVolumeTransformer(
            voice.source, volume=global_volume)

    await ctx.send(video_link)


@slash.slash(name="pause")
async def pause(ctx=SlashContext):
    voice = ctx.voice_client
    if voice.is_playing():
        voice.pause()
        await ctx.send("Paused")
    else:
        await ctx.send("No song is currently playing")


@slash.slash(name="resume")
async def resume(ctx):
    voice = ctx.voice_client
    if voice.is_paused():
        voice.resume()
    await ctx.send("Resumed")


@slash.slash(name="volume")
async def volume(ctx=SlashContext,*,value: int = 0):
    global global_volume
    voice = ctx.voice_client
    global_volume = float(value)/100
    voice.source.volume = 1
    voice.source = discord.PCMVolumeTransformer(
        voice.source, volume=global_volume)
    print(global_volume)
    await ctx.send("Changing volume to "+str(voice.source.volume*100)+"%")


@slash.slash(name="stop")
async def stop(ctx):
    global global_volume
    global_volume = 1
    await ctx.voice_client.disconnect()
    await ctx.send("Disconnected")


@ bot.event
async def on_ready():
    print('client ready')

print("Running bot")

bot.run(TOKEN)
