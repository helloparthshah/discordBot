#!/usr/bin/python3
import urllib.request
from io import BytesIO
import random
from PIL import Image
import requests
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
from datetime import datetime
from discord.ext import tasks
from discord_slash.context import ComponentContext
from discord_slash.utils.manage_components import create_button, create_actionrow, create_select, create_select_option
from discord_slash.model import ButtonStyle
import openai
from revChatGPT.ChatGPT import Chatbot

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
REMOVE_BG_KEY = os.getenv('REMOVE_BG_KEY')
OPEN_WEATHER_KEY = os.getenv('OPEN_WEATHER_KEY')
global_volume = 1.0

bot = Client(intents=Intents.default())
slash = SlashCommand(bot, sync_commands=True)
_queue = []

YDL_OPTIONS = {'format': 'bestaudio', 'noplaylist': 'True'}
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', 'options': '-vn'}


class VideoLink:
    def __init__(self, link, thumbnail):
        self.link = link
        self.thumbnail = thumbnail


play_actions = [
    create_actionrow(
        create_button(
            style=ButtonStyle.red, label="Pause"),
        create_button(
            style=ButtonStyle.blue, label="Next"),
        create_button(
            style=ButtonStyle.red, label="Stop"),
        create_button(
            style=ButtonStyle.green, label="Seek +10s"),
    ), create_actionrow(
        create_select(
            options=[
                create_select_option("10", value="10%"),
                create_select_option("20", value="20%"),
                create_select_option("30", value="30%"),
                create_select_option("40", value="40%"),
                create_select_option("50", value="50%"),
                create_select_option("60", value="60%"),
                create_select_option("70", value="70%"),
                create_select_option("80", value="80%"),
                create_select_option("90", value="90%"),
                create_select_option("100", value="100%"),
            ],
            placeholder="Change volume",
        ),)]

pause_actions = [create_actionrow(
    create_button(
        style=ButtonStyle.green, label="Resume"),
    create_button(
        style=ButtonStyle.blue, label="Next"),
    create_button(
        style=ButtonStyle.red, label="Stop"),
    create_button(
        style=ButtonStyle.green, label="Seek +10s"),
), create_actionrow(
    create_select(
        options=[
            create_select_option("10", value="10%"),
            create_select_option("20", value="20%"),
            create_select_option("30", value="30%"),
            create_select_option("40", value="40%"),
            create_select_option("50", value="50%"),
            create_select_option("60", value="60%"),
            create_select_option("70", value="70%"),
            create_select_option("80", value="80%"),
            create_select_option("90", value="90%"),
            create_select_option("100", value="100%"),
        ],
        placeholder="Change volume",
    ),
)]


@slash.slash(name="play", description="Play a song from YouTube",)
async def play(ctx=SlashContext, *, query=None):
    global global_volume
    await ctx.send("Playing")
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

    if(voice.is_playing() or voice.is_paused()):
        _queue.append(VideoLink(
            video_link, search_response['items'][0]['snippet']['thumbnails']['default']['url']))
        print(_queue)
        embed = discord.Embed(
            title="Added to queue", url=video_link, color=0x00ff00)
        embed.set_author(name=ctx.author.name,
                         icon_url=ctx.author.avatar_url)
        embed.set_thumbnail(
            url=search_response['items'][0]['snippet']['thumbnails']['default']['url'])
        embed.add_field(name="Queue length :", value=len(_queue), inline=True)
        embed.set_footer(text="Volume : " + str(
            global_volume*100)+"%")
        await ctx.send(embed=embed, components=pause_actions if voice.is_paused() else play_actions)
        return

    with YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(video_link, download=False)
        URL = info['formats'][0]['url']
        voice.play(FFmpegPCMAudio(URL, **FFMPEG_OPTIONS),
                   after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))
        voice.is_playing()

        print(global_volume)
        # voice.source.volume = 1
        voice.source = discord.PCMVolumeTransformer(
            voice.source, volume=global_volume)
    # Send emebed video link and title as song name
    embed = discord.Embed(
        title=info['title'], url=video_link, color=0x00ff00)
    embed.set_author(name=ctx.author.name, icon_url=ctx.author.avatar_url)
    embed.set_thumbnail(url=info['thumbnail'])
    embed.add_field(name="Queue length :", value=len(_queue), inline=True)
    embed.set_footer(text="Volume : " + str(
        global_volume*100)+"%")
    await ctx.send(embed=embed, components=play_actions)


async def play_next(ctx=SlashContext):
    voice = ctx.voice_client
    if(len(_queue) >= 1):
        video = _queue.pop(0)
        info = YoutubeDL(YDL_OPTIONS).extract_info(
            video.link, download=False)
        URL = info['formats'][0]['url']
        voice.play(FFmpegPCMAudio(URL, **FFMPEG_OPTIONS),
                   after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))
        global global_volume
        print(global_volume)
        # voice.source.volume = 1
        voice.source = discord.PCMVolumeTransformer(
            voice.source, volume=global_volume)
        embed = discord.Embed(
            title=info['title'], url=info['webpage_url'], color=0x00ff00)
        embed.set_author(name=ctx.author.name, icon_url=ctx.author.avatar_url)
        embed.set_thumbnail(url=info['thumbnail'])
        embed.add_field(name="Queue length :", value=len(_queue), inline=True)
        embed.set_footer(text="Volume : " + str(
            global_volume*100)+"%")
        await ctx.send(embed=embed, components=play_actions)
    else:
        # await asyncio.sleep(90)  # wait 1 minute and 30 seconds
        if not voice.is_playing():
            embed = discord.Embed(
                title="No more songs in queue", color=0x00ff00)
            embed.set_author(name=ctx.author.name,
                             icon_url=ctx.author.avatar_url)
            asyncio.run_coroutine_threadsafe(
                ctx.voice_client.disconnect(), bot.loop)
            asyncio.run_coroutine_threadsafe(
                await ctx.send(embed=embed), bot.loop)


@slash.slash(name="next", description="Play the next song in queue")
async def next(ctx=SlashContext):
    if(not ctx.author.voice):
        embed = discord.Embed(title="Join a channel first", color=0x00ff00)
        return await ctx.send(embed=embed)

    if(len(_queue) == 0):
        embed = discord.Embed(title="No more songs in queue", color=0x00ff00)
        return await ctx.send(embed=embed)

    # voice = get(bot.voice_clients, guild=ctx.guild)
    channel = ctx.author.voice.channel

    if(not ctx.voice_client):
        voice = await channel.connect()
    else:
        voice = ctx.voice_client
        voice.stop()

    await ctx.send("Playing next song")

    with YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(_queue.pop().link, download=False)
        embed = discord.Embed(
            title=info['title'], url=_queue[0].link, color=0x00ff00)
        embed.set_author(name=ctx.author.name, icon_url=ctx.author.avatar_url)
        embed.set_thumbnail(url=info['thumbnail'])
        embed.add_field(name="Queue length :", value=len(_queue), inline=True)
        embed.set_footer(text="Volume : " + str(
            global_volume*100)+"%")
        await ctx.send(embed=embed, components=play_actions)
        URL = info['formats'][0]['url']
        voice.play(FFmpegPCMAudio(URL, **FFMPEG_OPTIONS))
        voice.is_playing()

        print(global_volume)
        voice.source.volume = 1
        voice.source = discord.PCMVolumeTransformer(
            voice.source, volume=global_volume)

    print(_queue)


@slash.slash(name="clear", description="Clear the queue")
async def clear(ctx=SlashContext):
    _queue.clear()
    embed = discord.Embed(title="Queue cleared", color=0x00ff00)
    embed.set_author(name=ctx.author.name, icon_url=ctx.author.avatar_url)
    await ctx.send(embed=embed)


@slash.slash(name="link", description="Play a song using a link")
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
        videoId = video_link.split('=')[1].split('&')[0]
        _queue.append(
            VideoLink(video_link, "https://img.youtube.com/vi/"+videoId+"/default.jpg"))
        print(_queue)
        embed = discord.Embed(
            title="Added to Queue", url=video_link, color=0x00ff00)
        embed.set_author(name=ctx.author.name, icon_url=ctx.author.avatar_url)
        embed.set_thumbnail(
            url="https://img.youtube.com/vi/"+videoId+"/default.jpg")
        embed.add_field(name="Volume", value=str(
            global_volume*100)+"%", inline=True)
        await ctx.send(embed=embed, components=pause_actions if voice.is_paused() else play_actions)
        return

    with YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(video_link, download=False)
        URL = info['formats'][0]['url']
        voice.play(FFmpegPCMAudio(URL, **FFMPEG_OPTIONS),
                   after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))
        voice.is_playing()

        print(global_volume)
        # voice.source.volume = 1
        voice.source = discord.PCMVolumeTransformer(
            voice.source, volume=global_volume)

    embed = discord.Embed(title=info['title'], url=video_link, color=0x00ff00)
    embed.set_author(name=ctx.author.name, icon_url=ctx.author.avatar_url)
    embed.set_thumbnail(url=info['thumbnail'])
    await ctx.send(embed=embed, components=play_actions)


@slash.slash(name="pause", description="Pause the current song")
async def pause(ctx=SlashContext):
    voice = ctx.voice_client
    if voice.is_playing():
        voice.pause()
        await ctx.send(embed=discord.Embed(
            title="Paused", color=0x00ff00))
    else:
        await ctx.send(embed=discord.Embed(
            title="Not playing", color=0x00ff00))


@slash.slash(name="resume", description="Resume the current song")
async def resume(ctx):
    voice = ctx.voice_client
    if voice.is_paused():
        voice.resume()
    await ctx.send(embed=discord.Embed(
        title="Resumed", color=0x00ff00))


@slash.slash(name="volume", description="Change the volume")
async def volume(ctx=SlashContext, *, value: int = 0):
    if not ctx.voice_client:
        return await ctx.send(embed=discord.Embed(
            title="Join a channel first", color=0x00ff00))
    global global_volume
    voice = ctx.voice_client
    global_volume = float(value)/100
    voice.source.volume = 1
    voice.source = discord.PCMVolumeTransformer(
        voice.source, volume=global_volume)
    print(global_volume)
    embed = discord.Embed(title="Volume changed to " +
                          str(voice.source.volume*100)+"%", color=0x00ff00)
    embed.set_author(name=ctx.author.name, icon_url=ctx.author.avatar_url)
    await ctx.send(embed=embed)


@slash.slash(name="stop", description="Stop the current song and leave the channel")
async def stop(ctx):
    global global_volume
    global_volume = 1
    await ctx.voice_client.disconnect()
    await ctx.send("Disconnected")


@slash.slash(name="queue", description="Show the current queue")
async def queue(ctx=SlashContext):
    if(len(_queue) == 0):
        await ctx.send("Queue is empty")
        return

    embed = discord.Embed(title="Queue", color=0x00ff00)
    embed.set_author(name=ctx.author.name, icon_url=ctx.author.avatar_url)
    for i in range(len(_queue)):
        embed.add_field(name=str(i+1)+". ", value=_queue[i].link, inline=False)
    await ctx.send(embed=embed)


@slash.slash(name="yo_mama", description="Yo mama")
async def yo_mama(ctx=SlashContext, *, user: discord.Member):
    # send a get request to https://api.yomomma.info/
    # read the response and send it as a message
    response = requests.get('https://api.yomomma.info/')
    data = response.json()
    await ctx.send(user.mention)
    await ctx.send(embed=discord.Embed(
        title=data['joke'], color=0x00ff00))

@slash.slash(name="porn", description="Porn PSA")
async def porn(ctx=SlashContext, *, user: discord.Member):
    await ctx.send("Dear " + user.mention)
    embed = discord.Embed(
        title="A Letter", color=0x00ff00)
    embed.add_field(name="Important", value="I am writing to you today to express my concern about your excessive consumption of pornography. While it is understandable that you may find watching porn to be a source of pleasure, it is important to understand that this behavior can have serious consequences on your mental health and relationships.\nResearch has shown that consuming too much pornography can lead to addiction, decreased sexual satisfaction, and a distorted perception of sexuality. It can also lead to feelings of guilt, shame, and anxiety, and may even contribute to the development of depression.\nFurthermore, excessive porn use can negatively impact your relationships with friends and family, and may even lead to problems in your romantic relationships. It is important to prioritize healthy communication and connection with those around you, rather than relying solely on the instant gratification of pornography.\nI urge you to consider the potential consequences of your behavior and to seek help if you feel that you are struggling to control your consumption of pornography. There are resources available to support you in overcoming this addiction and developing healthier habits.\nSincerely,\nChatGPT", inline=False)
    await ctx.send(embed=embed)

@slash.slash(name="rickroll", description="Never gonna give you up")
async def rickroll(ctx=SlashContext, *, link: str, user: discord.Member):
    await ctx.send(user.mention)
    await ctx.send("https://www.latlmes.com/breaking/"+link.replace(" ", "-"))


@slash.slash(name="remove_bg", description="Remove the background")
async def remove_bg(ctx=SlashContext, *, user: discord.Member):
    # User remove.bg to remove the background
    # TypeError: a bytes-like object is required, not 'coroutine'
    image = Image.open(BytesIO(requests.get(user.avatar_url).content))
    image.save('temp.png')
    response = requests.post(
        'https://api.remove.bg/v1.0/removebg',
        files={'image_file': open('./temp.png', 'rb')},
        data={'size': 'auto'},
        headers={'X-Api-Key': REMOVE_BG_KEY},
    )
    if response.status_code == requests.codes.ok:
        with open('temp.png', 'wb') as out:
            out.write(response.content)
        await ctx.send(file=discord.File('temp.png'))


@slash.slash(name="weather", description="Get the weather")
async def weather(ctx=SlashContext, *, city: str):
    response = requests.get(
        'http://api.openweathermap.org/data/2.5/weather?q='+city+'&appid='+OPEN_WEATHER_KEY)
    data = response.json()
    embed = discord.Embed(title="Weather", color=0x00ff00)
    embed.set_author(name=ctx.author.name, icon_url=ctx.author.avatar_url)
    embed.add_field(name="City", value=city, inline=False)
    embed.add_field(name="Temperature", value=str(
        round(data['main']['temp']-273.15))+"°C", inline=False)
    embed.add_field(name="Description",
                    value=data['weather'][0]['description'], inline=False)
    await ctx.send(embed=embed)


@slash.slash(name="remind_me", description="Remind you about something after a certain time")
async def remind_me(ctx=SlashContext, *, time: str, message: str):
    embed = discord.Embed(title="Reminder", color=0x00ff00)
    embed.set_author(name=ctx.author.name, icon_url=ctx.author.avatar_url)
    embed.add_field(name="Message", value=message, inline=False)
    embed.add_field(name="Time", value=time, inline=False)
    await ctx.send(embed=embed)
    await asyncio.sleep(int(time))
    await ctx.send(ctx.author.mention)
    await ctx.send(message)


def getStreams(game):
    payload = [{
        "operationName": "DirectoryPage_Game",
        "variables": {
            "imageWidth": 50,
            "name": game,
            "options": {
                "sort": "RELEVANCE",
                "recommendationsContext": {
                    "platform": "web"
                },
                "requestID": "JIRA-VXP-2397",
                "freeformTags": None,
                "tags": [
                    "c2542d6d-cd10-4532-919b-3d19f30a768b"
                ]
            },
            "freeformTagsEnabled": False,
            "sortTypeIsRecency": False,
            "limit": 5
        },
        "extensions": {
            "persistedQuery": {
                "version": 1,
                "sha256Hash": "749035333f1837aca1c5bae468a11c39604a91c9206895aa90b4657ab6213c24"
            }
        }
    }]
    res = requests.post(
        'https://gql.twitch.tv/gql', json=payload, headers={
            "Client-Id": "kimne78kx3ncx6brgo4mv6wki5h1ko"
        }
    ).json()

    return res[0]['data']['game']['streams']['edges']


@slash.slash(name="drops", description="Check for streams that have drops enabled for a game")
async def drops(ctx=SlashContext, *, game: str):
    embed = discord.Embed(title=game+" streams", color=0x00ff00)
    for stream in getStreams(game):
        if(stream['node']['broadcaster']):
            embed.add_field(name=stream['node']['title'], value="https://www.twitch.tv/" +
                            stream['node']['broadcaster']['login'], inline=False)
    await ctx.send(embed=embed, components=[
        create_actionrow(
            create_button(
                style=ButtonStyle.URL,
                label="Open in browser",
                url="https://www.twitch.tv/directory/game/" +
                    game.replace(" ", "%20") +
                "?tl=DropsEnabled"
            ),
            create_button(
                style=ButtonStyle.blue,
                label="Refresh",
            )
        )
    ])

games = {'Fortnite': 1,
         'Rocket League': 1,
         'Overwatch 2': 1,
         'Valorant': 1}

buttons = [
    create_button(
        style=ButtonStyle.green,
        label="Choose again",
    ),
    create_button(
        style=ButtonStyle.red,
        label="Remove choice and choose again",
    ),
]
action_row = create_actionrow(*buttons)


@slash.slash(name="choose", description="Choose a game")
async def choose(ctx=SlashContext):
    choice = random.choices(
        list(games.keys()), weights=list(games.values()), k=1)[0]

    await ctx.send(embed=discord.Embed(title="I choose "+choice, color=0x00ff00), components=[action_row])


@bot.event
async def on_component(ctx: ComponentContext):
    global global_volume
    # await ctx.edit_origin(embed=discord.Embed(title=ctx.origin_message.embeds[0].title, color=0x00ff00), components=[])
    # check if button or select
    if ctx.component_type == 3:
        if not ctx.voice_client:
            return await ctx.send(embed=discord.Embed(
                title="Join a channel first", color=0x00ff00))
        voice = ctx.voice_client
        global_volume = int(ctx.selected_options[0].replace("%", ""))/100
        global_volume = round(global_volume, 1)
        is_paused = voice.is_paused()
        voice.source.volume = 1
        voice.source = discord.PCMVolumeTransformer(
            voice.source, volume=global_volume)
        # pause if it was paused
        if(is_paused):
            voice.pause()
        print(global_volume)
        embed = ctx.origin_message.embeds[0].set_footer(
            text="Volume: "+str(global_volume*100)+"%")
        await ctx.edit_origin(embed=embed, components=pause_actions if voice.is_paused() else play_actions)
    elif ctx.component_type == 2:
        if ctx.component['label'] == "Choose again":
            await ctx.origin_message.delete()
            choice = random.choices(
                list(games.keys()), weights=list(games.values()), k=1)[0]
            await ctx.send(embed=discord.Embed(title="I choose "+choice, color=0x00ff00), components=[action_row])
        elif ctx.component['label'] == "Remove choice and choose again":
            del games[ctx.origin_message.embeds[0].title.replace(
                "I choose ", "")]
            await ctx.origin_message.delete()
            choice = random.choices(
                list(games.keys()), weights=list(games.values()), k=1)[0]
            await ctx.send(embed=discord.Embed(title="I choose "+choice, color=0x00ff00), components=[action_row])
        elif ctx.component['label'] == "Pause":
            # get the voice client from the guild
            voice = ctx.voice_client
            if(voice):
                if voice.is_playing():
                    voice.pause()
                    await ctx.edit_origin(embed=ctx.origin_message.embeds[0], components=pause_actions)
                else:
                    await ctx.send(embed=discord.Embed(
                        title="Not playing", color=0x00ff00))
        elif ctx.component['label'] == "Resume":
            voice = ctx.voice_client
            if(voice):
                if voice.is_paused():
                    voice.resume()
                    await ctx.edit_origin(embed=ctx.origin_message.embeds[0], components=play_actions)
        elif ctx.component['label'] == "Next":
            if(not ctx.author.voice):
                embed = discord.Embed(
                    title="Join a channel first", color=0x00ff00)
                return await ctx.send(embed=embed)

            if(len(_queue) == 0):
                embed = discord.Embed(
                    title="No more songs in queue", color=0x00ff00)
                return await ctx.send(embed=embed)

            # voice = get(bot.voice_clients, guild=ctx.guild)
            channel = ctx.author.voice.channel

            if(not ctx.voice_client):
                voice = await channel.connect()
            else:
                voice = ctx.voice_client
                voice.stop()

            await ctx.send("Playing next song")

            with YoutubeDL(YDL_OPTIONS) as ydl:
                info = ydl.extract_info(_queue.pop().link, download=False)
                embed = discord.Embed(
                    title=info['title'], url=_queue[0].link, color=0x00ff00)
                embed.set_author(name=ctx.author.name,
                                 icon_url=ctx.author.avatar_url)
                embed.set_thumbnail(url=info['thumbnail'])
                embed.add_field(name="Queue length :",
                                value=len(_queue), inline=True)
                embed.set_footer(text="Volume : " + str(
                    global_volume*100)+"%")
                await ctx.send(embed=embed, components=play_actions)
                URL = info['formats'][0]['url']
                voice.play(FFmpegPCMAudio(URL, **FFMPEG_OPTIONS))
                voice.is_playing()

                print(global_volume)
                voice.source.volume = 1
                voice.source = discord.PCMVolumeTransformer(
                    voice.source, volume=global_volume)

            print(_queue)
        elif ctx.component['label'] == "Stop":
            global_volume = 1
            await ctx.voice_client.disconnect()
            await ctx.send("Disconnected")
        elif ctx.component['label'] == "Refresh":
            game = ctx.origin_message.embeds[0].title.replace(" streams", "")
            embed = discord.Embed(title=game+" streams", color=0x00ff00)
            for stream in getStreams(game):
                if(stream['node']['broadcaster']):
                    embed.add_field(name=stream['node']['title'], value="https://www.twitch.tv/" +
                                    stream['node']['broadcaster']['login'], inline=False)
            await ctx.edit_origin(embed=embed, components=[
                create_actionrow(
                    create_button(
                        style=ButtonStyle.URL,
                        label="Open in browser",
                        url="https://www.twitch.tv/directory/game/" +
                            game.replace(" ", "%20") +
                        "?tl=DropsEnabled"
                    ),
                    create_button(
                        style=ButtonStyle.blue,
                        label="Refresh",
                    )
                )
            ])
        elif ctx.component['label'] == "Volume Up":
            if not ctx.voice_client:
                return await ctx.send(embed=discord.Embed(
                    title="Join a channel first", color=0x00ff00))
            voice = ctx.voice_client
            global_volume = global_volume+0.1
            global_volume = round(global_volume, 1)
            is_paused = voice.is_paused()
            voice.source.volume = 1
            voice.source = discord.PCMVolumeTransformer(
                voice.source, volume=global_volume)
            # pause if it was paused
            if(is_paused):
                voice.pause()
            print(global_volume)
            embed = ctx.origin_message.embeds[0].set_footer(
                text="Volume: "+str(global_volume*100)+"%")
            await ctx.edit_origin(embed=embed, components=pause_actions if voice.is_paused() else play_actions)

        elif ctx.component['label'] == "Volume Down":
            if not ctx.voice_client:
                return await ctx.send(embed=discord.Embed(
                    title="Join a channel first", color=0x00ff00))
            voice = ctx.voice_client
            global_volume = global_volume-0.1
            # round to 1 decimal
            global_volume = round(global_volume, 1)
            if(global_volume < 0):
                global_volume = 0
            is_paused = voice.is_paused()
            voice.source.volume = 1
            voice.source = discord.PCMVolumeTransformer(
                voice.source, volume=global_volume)
            # pause if it was paused
            if(is_paused):
                voice.pause()
            print(global_volume)
            embed = ctx.origin_message.embeds[0].set_footer(
                text="Volume: "+str(global_volume*100)+"%")
            await ctx.edit_origin(embed=embed, components=pause_actions if voice.is_paused() else play_actions)
        elif ctx.component['label'] == "Seek +10s":
            if not ctx.voice_client:
                return await ctx.send(embed=discord.Embed(
                    title="Join a channel first", color=0x00ff00))
            voice = ctx.voice_client
            for i in range(0, 500):
                voice.source.read()
            await ctx.edit_origin(embed=ctx.origin_message.embeds[0], components=pause_actions if voice.is_paused() else play_actions)


@ slash.slash(name="games", description="Show list of games")
async def listgames(ctx=SlashContext):
    global games
    total = sum(games.values())
    embed = discord.Embed(title="Games", color=0x00ff00)
    for game in games:
        embed.add_field(name=game, value=games[game], inline=False)
    await ctx.send(embed=embed)


@ slash.slash(name="addgame", description="Add new game")
async def addgame(ctx=SlashContext, *, game: str, weight: int):
    games[game] = weight
    total = sum(games.values())
    embed = discord.Embed(title="Games", color=0x00ff00)
    for game in games:
        embed.add_field(name=game, value=games[game], inline=False)
    await ctx.send(embed=embed)


@ slash.slash(name="removegame", description="Remove existing game")
async def removegame(ctx=SlashContext, *, game: str):
    for g in games:
        if(g.lower().replace(" ", "") == game.lower().replace(" ", "")):
            del games[g]
            break
    total = sum(games.values())
    embed = discord.Embed(title="Games", color=0x00ff00)
    for game in games:
        embed.add_field(name=game, value=games[game], inline=False)
    await ctx.send(embed=embed)


@ slash.slash(name="changeweight", description="Change weight for game")
async def addgame(ctx=SlashContext, *, game: str, weight: int):
    for g in games:
        if(g.lower().replace(" ", "") == game.lower().replace(" ", "")):
            games[g] = weight
            break
    total = sum(games.values())
    embed = discord.Embed(title="Games", color=0x00ff00)
    for game in games:
        embed.add_field(name=game, value=games[game], inline=False)
    await ctx.send(embed=embed)


@ slash.slash(name="rlrank", description="Get ranks for rocket league")
async def rlrank(ctx=SlashContext, *, epicid: str):
    # spoof that we are a browser
    data = requests.get(
        "https://api.tracker.gg/api/v2/rocket-league/standard/profile/epic/"+epicid,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "max-age=0",
            "DNT": "1",
            "If-Modified-Since": "Mon, 02 Jan 2023 13:20:32 GMT",
            "Sec-Ch-Ua": "\"Not?A_Brand\";v=\"8\", \"Chromium\";v=\"108\", \"Google Chrome\";v=\"108\"",
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": "\"Windows\"",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "Cookie": os.environ['RL_COOKIE']
        }
    )
    print(data.text)
    data = data.json()
    ranks = []
    for mode in data['data']['segments']:
        if(mode['type'] == "playlist"):
            ranks.append([mode['metadata']['name'],
                          mode['stats']['tier']['metadata']['name'],
                          mode['stats']['division']['metadata']['name'],
                          mode['stats']['tier']['value'],
                          mode['stats']['tier']['metadata']['iconUrl']])
    highest = ranks[0]
    for rank in ranks:
        if(rank[3] > highest[3]):
            highest = rank
    embed = discord.Embed(title="Ranks for "+epicid, color=0x00ff00)
    for rank in ranks:
        embed.add_field(name=rank[0], value=rank[1]+" "+rank[2], inline=False)
    embed.set_thumbnail(url=highest[4])
    await ctx.send(embed=embed)

""" chatbot = Chatbot({
    "session_token": os.getenv('session_token')
}, conversation_id=None, parent_id=None)


@ slash.slash(name="chat", description="Talk to the bot")
async def chat(ctx=SlashContext, *, message: str):
    response = chatbot.ask(message, conversation_id=None, parent_id=None)
    print(response)
    ctx.send(message+'\n' + response) """

openai.api_key = os.getenv("OPENAI_API_KEY")


@ slash.slash(name="writecode", description="Helps you write code")
async def chat(ctx=SlashContext, *, message: str):
    input_prompt = (
        f"Complete the code for the comment\n"
        f"### Comment"
        f"\n"
        f"{message}"
        f"\n"
        f"### Code"
    )
    await ctx.send("Hmmm...")
    response = openai.Completion.create(
        model="code-davinci-002",
        prompt=input_prompt,
        temperature=0,
        max_tokens=256,
        top_p=1,
        frequency_penalty=0,
        presence_penalty=0,
        stop=["###"]
    )
    await ctx.send(message+"```"+response['choices'][0]['text']+"```")


@ slash.slash(name="atharavsolutions", description="Helps you cheat on your homework")
async def chat(ctx=SlashContext):
    test = """Attention all computer science students! Are you tired of losing grades and facing academic penalties for copying code? If yes, Atharav Solutions is here to help you.

Atharav, a student just like you, faced the same problem and failed a class three times due to his inability to copy code effectively. He was determined to find a solution and created Atharav Solutions to help other students like him.

Our company specializes in changing code so it does not look like copied code, helping you avoid plagiarism penalties. With Atharav Solutions, you can say goodbye to lost grades and hello to successful academic careers.

Our team of experts uses innovative techniques to ensure that your code is unique and original, and that you receive the grades you deserve. With Atharav Solutions, you can focus on learning and growing in your computer science studies without the fear of academic penalties.

So, if you're ready to put an end to the problem of copying code and succeed in your computer science classes, choose Atharav Solutions. Contact us today and let us help you achieve your academic goals!

https://atharav-solutions.onrender.com/
    """
    await ctx.send(test)


@ slash.slash(name="image", description="Generate an image")
async def image(ctx=SlashContext, *, message: str):
    response = openai.Image.create(
        prompt=message,
        n=1,
        size="1024x1024"
    )
    image_url = response['data'][0]['url']
    ctx.send(image_url)


@ slash.slash(name="githubstats", description="Get github stats")
async def githubstats(ctx=SlashContext, *, username: str):
    await ctx.send("Hmmm...")
    data = requests.get("https://api.github.com/users/"+username)
    data = data.json()
    embed = discord.Embed(title="Github stats for "+username, color=0x00ff00)
    embed.add_field(name="Followers", value=data['followers'], inline=False)
    embed.add_field(name="Following", value=data['following'], inline=False)
    embed.add_field(name="Public repos",
                    value=data['public_repos'], inline=False)
    embed.add_field(name="Public gists",
                    value=data['public_gists'], inline=False)
    embed.add_field(name="Bio", value=data['bio'], inline=False)
    embed.set_thumbnail(url=data['avatar_url'])
    await ctx.send(embed=embed)
    await ctx.send("https://streak-stats.demolab.com/?user="+username+"&theme=radical&type=png")


@ slash.slash(name="help", description="View all of the commands")
async def help(ctx=SlashContext):
    # dynamically create the embed
    embed = discord.Embed(title="Help", color=0x00ff00)
    for command in slash.commands:
        if(slash.commands[command]):
            embed.add_field(name="/"+command,
                            value=slash.commands[command].description, inline=False)
    await ctx.send(embed=embed)

Started = False
isOn = False


@ tasks.loop(minutes=5)
async def reminder():
    global Started, isOn
    print("Reminder", Started, isOn)
    streams = getStreams("rocket league")
    if(len(streams) > 1 and not Started):
        if isOn:
            Started = True
            channel = bot.get_channel(1006713461474066513)
            allowed_mentions = discord.AllowedMentions(everyone=True)
            await channel.send(content="@everyone", allowed_mentions=allowed_mentions)
            embed = discord.Embed(
                title="Rocket League drops are live!", color=0x00ff00)
            for stream in streams:
                if(stream['node']['broadcaster']):
                    embed.add_field(name=stream['node']['title'], value="https://www.twitch.tv/" +
                                    stream['node']['broadcaster']['login'], inline=False)
            await channel.send(embed=embed, components=[
                create_actionrow(
                    create_button(
                        style=ButtonStyle.URL,
                        label="Open in browser",
                        url="https://www.twitch.tv/directory/game/rocket%20league?tl=DropsEnabled"
                    ),
                    create_button(
                        style=ButtonStyle.blue,
                        label="Refresh",
                    )
                )
            ])
        else:
            isOn = True
    elif(len(streams) == 0):
        Started = False
        isOn = False


@ bot.event
async def on_message(message):
    mention = str(bot.user.id)
    if mention in message.content:
        embed = discord.Embed(
            title="Thank you for using my bot",
            description="I am a bot created by <@!279174239972491276>",)
        await message.channel.send(embed=embed)


@ bot.event
async def on_ready():
    print('client ready')
    await bot.change_presence(activity=discord.Game(name=f"/help"))
    reminder.start()

print("Running bot")

bot.run(TOKEN)
