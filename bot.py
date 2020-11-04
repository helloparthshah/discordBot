#!/usr/bin/python3
from googleapiclient.discovery import build
import os

import discord
from dotenv import load_dotenv
from discord.ext import commands
from discord.utils import get
from discord import FFmpegPCMAudio
from youtube_dl import YoutubeDL

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
YT_KEY = os.getenv('YT_KEY')
global_volume = 1.0

bot = commands.Bot(command_prefix="/")


@bot.command()
async def play(ctx, *, query=None):
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

    youtube = build("youtube", "v3", developerKey=YT_KEY)
    search_response = youtube.search().list(
        q=query, part="id,snippet", maxResults=1).execute()

    vid = search_response['items'][0]['id']['videoId']
    # It will send the data in a .json format.
    video_link = 'https://www.youtube.com/watch?v=' + vid

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

    await ctx.channel.send('https://www.youtube.com/watch?v=' + vid)


@bot.command()
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


@bot.event
async def on_ready():
    print('client ready')

print("Running bot")

bot.run(TOKEN)
