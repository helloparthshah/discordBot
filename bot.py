#!/usr/bin/python3
from io import BytesIO
from urllib import response
from PIL import Image
from unicodedata import name
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


@slash.slash(name="play", description="Play a song from YouTube",)
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
        embed = discord.Embed(
            title="Added to queue", color=0x00ff00)
        embed.set_author(name=ctx.author.name,
                         icon_url=ctx.author.avatar_url)
        embed.set_thumbnail(
            url=search_response['items'][0]['snippet']['thumbnails']['default']['url'])
        await ctx.send(embed=embed)
        # ctx.send("Added to queue")
        # await ctx.send(video_link)
        return

    with YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(video_link, download=False)
        URL = info['formats'][0]['url']
        voice.play(FFmpegPCMAudio(URL, **FFMPEG_OPTIONS),
                   after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))
        voice.is_playing()

        global global_volume
        print(global_volume)
        # voice.source.volume = 1
        voice.source = discord.PCMVolumeTransformer(
            voice.source, volume=global_volume)
    # Send emebed video link and title as song name
    embed = discord.Embed(title=info['title'], url=video_link, color=0x00ff00)
    embed.set_author(name=ctx.author.name, icon_url=ctx.author.avatar_url)
    embed.set_thumbnail(url=info['thumbnail'])
    await ctx.send(embed=embed)


async def play_next(ctx=SlashContext):
    voice = ctx.voice_client
    if(len(_queue) >= 1):
        info = YoutubeDL(YDL_OPTIONS).extract_info(
            _queue.pop(0), download=False)
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
        await ctx.send(embed=embed)
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

    with YoutubeDL(YDL_OPTIONS) as ydl:
        embed = discord.Embed(
            title=info['title'], url=_queue[0], color=0x00ff00)
        embed.set_author(name=ctx.author.name, icon_url=ctx.author.avatar_url)
        embed.set_thumbnail(url=info['thumbnail'])
        await ctx.send(embed=embed)
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
        _queue.append(video_link)
        print(_queue)
        await ctx.send(video_link)
        return

    with YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(video_link, download=False)
        URL = info['formats'][0]['url']
        voice.play(FFmpegPCMAudio(URL, **FFMPEG_OPTIONS),
                   after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))
        voice.is_playing()

        global global_volume
        print(global_volume)
        # voice.source.volume = 1
        voice.source = discord.PCMVolumeTransformer(
            voice.source, volume=global_volume)

    await ctx.send(video_link)


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
        embed.add_field(name=str(i+1)+". ", value=_queue[i], inline=False)
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


@slash.slash(name="course", description="Get the course information")
async def course(ctx=SlashContext, *, course: str):
    # https://mydegree.ucdavis.edu/responsiveDashboard/api/course-link?discipline=ECS&number=160
    embed = discord.Embed(title="Course", color=0x00ff00)
    embed.set_author(name=ctx.author.name, icon_url=ctx.author.avatar_url)
    embed.add_field(
        name=course, value="Please wait while we fetch the details for "+course, inline=False)
    await ctx.send(embed=embed)
    try:
        response = requests.get(
            'https://mydegree.ucdavis.edu/responsiveDashboard/api/course-link?discipline=' +
            course.split(' ')[0]+'&number='+course.split(' ')[1]+'&',
            headers={'Cookie': 'NAME=Parth%20Ninad%20Shah; REFRESH_TOKEN=Bearer+eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI5MTY4MjkxNzEiLCJpbnRlcm5hbElkIjoiOTE2ODI5MTcxIiwidXNlckNsYXNzIjoiU1RVIiwiYXBwTmFtZSI6ImRlZ3JlZXdvcmtzIiwibmFtZSI6IlNoYWgsIFBhcnRoIE5pbmFkIiwiZXhwaXJlSW5jcmVtZW50U2Vjb25kcyI6NTk5OTQwLCJleHAiOjE2NDc5MTk4MDksImFsdElkIjoicGFydGgxMjMiLCJpYXQiOjE2NDczMTk4NjksImp0aSI6ImNlNzA1N2VmLWI3ZmItNDEzMy05YTM0LTM0MDlhZDA2NzkyZSJ9.RtaH12n9SntJg24SdnbvpSeTrUrgs1z0-SBUC0MeUPA; X-AUTH-TOKEN=Bearer+eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI5MTY4MjkxNzEiLCJpbnRlcm5hbElkIjoiOTE2ODI5MTcxIiwidXNlckNsYXNzIjoiU1RVIiwiYXBwTmFtZSI6ImRlZ3JlZXdvcmtzIiwicm9sZXMiOlsiU0RBVURJVCIsIlNEQVVEUkVWIiwiU0RXSEFUSUYiLCJOT1JFRkVSIiwiU0RXSUZISVMiLCJTRFdPUktTIiwiU0RYTUwzMyIsIlNEQVVEUlVOIiwiU0RYTUwzMSIsIlNEQVVEUERGIiwiU0RMT0tBSEQiLCJTRFdFQjMxIiwiU0RXRUIzMyIsIlNEU1RVTUUiLCJTRFdFQjM2Il0sIm5hbWUiOiJTaGFoLCBQYXJ0aCBOaW5hZCIsImRlcGFydG1lbnRzIjpbXSwiZXhwIjoxNjQ3OTIwMDA3LCJhbHRJZCI6InBhcnRoMTIzIiwiaWF0IjoxNjQ3MzIwMDY3LCJqdGkiOiI2NjdmODU1Zi01NjVlLTQ3YWMtYjYwMy0wZGQwMjMyMmI2ZTcifQ.pX4LNCbTrPoJDHWVlKcWuwo23fuHV9oPEH_puToag4E; JSESSIONID=A2F4666A9E4A939FDA4613C9F0C8EF6B; _hjSessionUser_648798=eyJpZCI6ImExMzk0ODA1LTY4MmQtNTdhMy1iMDdhLTgwMjM0NTM1YTJkOCIsImNyZWF0ZWQiOjE2Mzg1NzE4MTk0MzksImV4aXN0aW5nIjp0cnVlfQ==; _hjSessionUser_653936=eyJpZCI6ImUxMDcyNWY3LTdlMDgtNWY0MC1iMjRmLTQxMTE3NzdkOTE3YiIsImNyZWF0ZWQiOjE2Mzg3NzI2OTU2ODUsImV4aXN0aW5nIjp0cnVlfQ==; __utmc=162187235; _hjSessionUser_648769=eyJpZCI6IjkzOWJmYmI5LTkxMjItNTc2Yi1hM2YzLTI0ZTE1YzliZDJkOCIsImNyZWF0ZWQiOjE2Mzk2MjYxMzAwNTksImV4aXN0aW5nIjp0cnVlfQ==; _hjSessionUser_656324=eyJpZCI6ImYyYzgxYzJmLWI2ZjQtNTFiMC1hMzk0LTJhYTM3MWVhMjA0ZiIsImNyZWF0ZWQiOjE2Mzk2Mjg2OTMwNTQsImV4aXN0aW5nIjp0cnVlfQ==; nmstat=aed74523-ab5d-f360-8392-3689cadc204e; citrix_ns_id=AAE7gX3UYTszlOcCAAAAADs9GSgA5c91NSPIO5hox76dPDDtnsvKaZO-SBVZEOITOw==B4HUYQ==XbODb_NCGQLnpHv_RJ5o2oh0K7A=; citrix_ns_id_.ucdavis.edu_%2F_wat=AAAAAAWOa83Xu667afSVnlKdioV6kBJzDq_SaGolWna8d5abXybuc354qBwMBtGXOPgmQjIt4vEs_WL-P1AAR44Q0JTo&AAAAAAUEUi_GkPTPKKW5aWHCz0JMcVLaM0-KqpSt8NK5AnaTux6vH-5f3XNzidGvbxG49UaxKQJ_Vd55R9DmHm_oxkb8RZ2iQc-B_D4VeGFYxrKnKg==&AAAAAAV9ZN2kjRuOWE1I1x2qwPWGgXE1lWVeTMtJP4rJwIF4eLpbB_YTJigwGCU3Z0xp662cUYMTTruU4Zag0lMUV9lf6rtudtCIVRpifwEsWa4v5g==&; SAP_CASAUTH=4D99A2C728D939B6A652E6CB7859C2BD43639A19F6CFCDD5FA2C60ADA08C02A1550FF5D61587732DEE15F2055B04E4513605DA27A66E9B974A4ECAF417DF27EA180B99A3164CF05F1BA3EBCA8FA0BAD92A2D7BA3EDEEDB3043FC6564032A3AF2; __utmz=162187235.1646345630.14.7.utmcsr=cas.ucdavis.edu|utmccn=(referral)|utmcmd=referral|utmcct=/; _fbp=fb.1.1646348587255.953116442; __utma=162187235.1828723012.1638322952.1646345630.1646349181.15; _ga=GA1.2.1828723012.1638322952; _ga_YQ20RZQYKR=GS1.1.1646354327.2.0.1646354327.60; _gid=GA1.2.273393137.1647237034; _hjSession_648798=eyJpZCI6IjliYTc2YTlhLTM1NzUtNGIxNi04N2Q0LTZiNmZlNjRlYzAzZCIsImNyZWF0ZWQiOjE2NDczMTk0MTg1NjIsImluU2FtcGxlIjp0cnVlfQ==; MYUCDAVIS_LANDING_IMAGE=bikesandtrees%2Ejpg%2Cblossoms%2Ejpg%2Cbunitransblur%2Ejpg%2Cccinterior%2Ejpg%2Ccpool%2Ejpg%2Ccwolfskill2%2Ejpg%2Cdeathblossom%2Ejpg%2Cdkayaks%2Ejpg%2Cdorms2%2Ejpg%2Cegret%2Ejpg%2Cflag2%2Ejpg%2CginsengMrak%2Ejpg%2Cgoags%2Ejpg%2Clibrary%2Ejpg%2Corchard%2Ejpg%2Cpallette%2Ejpg%2Cpcircle%2Ejpg%2Cplantsciences%2Ejpg%2Csciencewall%2Ejpg%2Csilo2%2Ejpg%2Cspokes%2Ejpg%2CstudentFarmPoppy%2EJPG%2Cterceroquad%2Ejpg; _gcl_au=1.1.1866842315.1647319836; _hjSession_653936=eyJpZCI6IjA4ZWMyNGQxLWZlYjItNDMxYS1hNTU0LWE1N2ZiYjg4Yjg0MCIsImNyZWF0ZWQiOjE2NDczMTk4MzYyNDksImluU2FtcGxlIjp0cnVlfQ=='})
        data = response.json()
        desc = data['courseInformation']['courses'][0]['description']
        sections = data['courseInformation']['courses'][0]['sections'][0]
        times = []
        days = ['monday', 'tuesday', 'wednesday',
                'thursday', 'friday', 'saturday', 'sunday']
        for meetings in sections['meetings']:
            beginTime= datetime.strptime(meetings['beginTime'], "%H%M")
            endTime= datetime.strptime(meetings['endTime'], "%H%M")
            for day in days:
                if meetings[day] != '':
                    times.append(
                        ("Lecture" if meetings['category']=="01" else "Discussion",day, beginTime.strftime("%I:%M %p"),endTime.strftime("%I:%M %p")))
        embed = discord.Embed(title="Course", color=0x00ff00)
        embed.set_author(name=ctx.author.name, icon_url=ctx.author.avatar_url)
        embed.add_field(name="Course", value=course, inline=False)
        embed.add_field(name="Description", value=desc, inline=False)
        embed.add_field(name="Times", value=times, inline=False)
        print(desc)
        await ctx.send(embed=embed)
    except:
        await ctx.send("Course not found")


@bot.event
async def on_message(message):
    mention = str(bot.user.id)
    if mention in message.content:
        if(str(message.author) == "GatoSecksual#6689"):
            await message.channel.send("🥫")
        else:
            embed = discord.Embed(
                title="Thank you for using my bot",
                description="I am a bot created by <@!279174239972491276>",)
            await message.channel.send(embed=embed)


@bot.event
async def on_ready():
    print('client ready')

print("Running bot")

bot.run(TOKEN)
