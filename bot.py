#!/usr/bin/python3
from googleapiclient.discovery import build
import os

import discord
from dotenv import load_dotenv
from discord.ext import commands
from discord.utils import get
from discord import FFmpegPCMAudio
from youtube_dl import YoutubeDL
import ctypes
import ctypes.util
import asyncio

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

bot = commands.Bot(command_prefix="/")

_queue = []

YDL_OPTIONS = {'format': 'bestaudio', 'noplaylist': 'True'}
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', 'options': '-vn'}


@bot.command()
async def play(ctx, *, query=None):
    if not query and ctx.voice_client.is_paused():
        return ctx.voice_client.resume()
    elif not query:
        return await ctx.channel.send("No song is currently playing")

    if(not ctx.author.voice):
        return await ctx.channel.send('Join a channel first')

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
        await ctx.channel.send(video_link)
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

    await ctx.channel.send(video_link)


def play_next(ctx):
    voice = ctx.voice_client
    if(len(_queue) >= 1):
        ctx.channel.send(_queue[0])
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

    else:
        asyncio.run_coroutine_threadsafe(voice.disconnect(ctx))
        asyncio.run_coroutine_threadsafe(
            ctx.channel.send("No more songs in queue."))


@ bot.command()
async def next(ctx, *, query=None):
    if(not ctx.author.voice):
        return await ctx.channel.send('Join a channel first')

    if(len(_queue) == 0):
        return await ctx.channel.send('No songs in queue')

    # voice = get(bot.voice_clients, guild=ctx.guild)
    channel = ctx.author.voice.channel

    if(not ctx.voice_client):
        voice = await channel.connect()
    else:
        voice = ctx.voice_client
        voice.stop()

    with YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(_queue[0], download=False)
        URL = info['formats'][0]['url']
        voice.play(FFmpegPCMAudio(URL, **FFMPEG_OPTIONS))
        voice.is_playing()

        global global_volume
        print(global_volume)
        voice.source.volume = 1
        voice.source = discord.PCMVolumeTransformer(
            voice.source, volume=global_volume)

    await ctx.channel.send(_queue[0])
    _queue.pop(0)
    print(_queue)


@ bot.command()
async def clear(ctx, *, query=None):
    _queue.clear()
    await ctx.channel.send('Cleard queue')


@ bot.command()
async def l(ctx, *, query=None):
    if not query and ctx.voice_client.is_paused():
        return ctx.voice_client.resume()
    elif not query:
        return await ctx.channel.send("No song is currently playing")

    if(not ctx.author.voice):
        return await ctx.channel.send('Join a channel first')

    YDL_OPTIONS = {'format': 'bestaudio', 'noplaylist': 'True'}
    FFMPEG_OPTIONS = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', 'options': '-vn'}
    # voice = get(bot.voice_clients, guild=ctx.guild)
    channel = ctx.author.voice.channel
    if(not ctx.voice_client):
        voice = await channel.connect()
    else:
        voice = ctx.voice_client
        voice.stop()

    # It will send the data in a .json format.
    video_link = query

    with YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(video_link, download=False)
    URL = info['formats'][0]['url']
    voice.play(FFmpegPCMAudio(URL, **FFMPEG_OPTIONS))
    voice.is_playing()

    global global_volume
    print(global_volume)
    voice.source.volume = 1
    voice.source = discord.PCMVolumeTransformer(
        voice.source, volume=global_volume)

    await ctx.channel.send(query)


@ bot.command()
async def pause(ctx):
    voice = ctx.voice_client
    if voice.is_playing():
        voice.pause()


@ bot.command()
async def resume(ctx):
    voice = ctx.voice_client
    if voice.is_paused():
        voice.resume()


@ bot.command()
async def volume(ctx, value: int):
    global global_volume
    voice = ctx.voice_client
    global_volume = float(value)/100
    voice.source.volume = 1
    voice.source = discord.PCMVolumeTransformer(
        voice.source, volume=global_volume)
    print(global_volume)
    await ctx.channel.send("Changing volume to "+str(voice.source.volume))


@ bot.command()
async def stop(ctx):
    global global_volume
    global_volume = 1
    await ctx.voice_client.disconnect()


@ bot.event
async def on_ready():
    print('client ready')

print("Running bot")

bot.run(TOKEN)
