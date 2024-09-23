#!/usr/bin/python3
from io import BytesIO
import random
from interactions import Intents, OptionType, listen, slash_command, slash_option
import interactions
import requests
import os
import discord
from dotenv import load_dotenv
import asyncio
from discord.ext import tasks
from interactions import Button, ButtonStyle, ActionRow
import google.generativeai as palm
from interactions import AutocompleteContext, SlashContext, Embed
from interactions.api.events import Component
from interactions.api.voice.audio import AudioVolume, Audio
from pytubefix import YouTube
import os
from youtube_search import YoutubeSearch
from PIL import Image, ImageFont, ImageDraw, ImageOps
import textwrap
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
import traceback
from interactions.api.events import CommandError

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
REMOVE_BG_KEY = os.getenv('REMOVE_BG_KEY')
OPEN_WEATHER_KEY = os.getenv('OPEN_WEATHER_KEY')
PALM_API_KEY = os.getenv('PALM_API_KEY')
PLAY_HT_KEY = os.getenv('PLAY_HT_KEY')
PLAY_HT_APP_ID = os.getenv('PLAY_HT_APP_ID')
MONGODB_DB_URL = os.getenv('MONGODB_DB_URL')

bot = interactions.Client()

cloned_voices = []

music_queue = {}

MongoDbClient = MongoClient(MONGODB_DB_URL, server_api=ServerApi('1'))

database = MongoDbClient["discord-bot"]

soundboardCollection = database["soundboard"]


class MusicQueueSong:
    def __init__(self, url):
        self.url = url
        self.yt = YouTube(url)


@slash_command(name="yo_mama", description="Yo mama")
@slash_option(
    name="user",
    description="The user to send the joke to",
    opt_type=OptionType.USER,
    required=True
)
async def yo_mama(ctx=SlashContext, *, user: discord.Member):
    await ctx.defer()
    # read the response and send it as a message
    response = requests.get(
        'https://www.yomama-jokes.com/api/v1/jokes/random/')
    data = response.json()
    print(data)
    await ctx.send(user.mention)
    await ctx.send(embed=interactions.Embed(
        title=data['joke'], color=0x00ff00))


@slash_command(name="tts", description="Text to speech")
@slash_option(
    name="text",
    description="String Option",
    required=True,
    opt_type=OptionType.STRING,
)
@slash_option(
    name="voice",
    description="The voice to use",
    required=False,
    opt_type=OptionType.STRING,
    autocomplete=True
)
async def tts(ctx=SlashContext, *, text: str, voice: str = "s3://voice-cloning-zero-shot/29652fa7-7a8c-4162-a69a-509d2b6bfc05/Harshil/manifest.json"):
    await ctx.defer()
    url = "https://api.play.ht/api/v2/tts/stream"
    payload = {
        "text": text,
        "voice": voice,
        "output_format": "mp3",
        "voice_engine": "PlayHT2.0"
    }
    headers = {
        "accept": "audio/mpeg",
        "content-type": "application/json",
        "AUTHORIZATION": PLAY_HT_KEY,
        "X-USER-ID": PLAY_HT_APP_ID
    }
    print("Connecting to voice channel")
    # if not joined a voice channel, join the user's voice channel

    print("Sending request")
    response = requests.post(url, json=payload, headers=headers)
    if response.status_code == 200:
        # save the audio file
        with open('tts.mp3', 'wb') as f:
            f.write(response.content)
        audio = AudioVolume('tts.mp3')
        await ctx.send(file=interactions.File('tts.mp3'))
        if (not ctx.author.voice):
            return
        if ctx.guild_id == ctx.author.voice.channel.guild.id:
            if not ctx.voice_state:
                await ctx.author.voice.channel.connect()
            else:
                await ctx.voice_state.move(ctx.author.voice.channel)
            await ctx.voice_state.play(audio)
    else:
        await ctx.send("Error: "+str(response.status_code))


@tts.autocomplete("voice")
async def autocomplete(ctx: AutocompleteContext):
    string_option_input = ctx.input_text  # can be empty/None
    # you can use ctx.kwargs.get("name") to get the current state of other options - note they can be empty too

    # make sure you respond within three seconds
    choices = []
    for voice in cloned_voices:
        choices.append({
            "name": voice['name'],
            "value": voice['id']
        })
    await ctx.send(
        choices=choices
    )


@slash_command(name="record", description="record some audio")
@slash_option(
    name="time",
    description="time in seconds to record",
    opt_type=OptionType.INTEGER,
    required=False
)
async def record(ctx: interactions.SlashContext, time: int = 10):
    await ctx.defer()
    if not ctx.author.voice:
        return await ctx.send('Join a channel first')

    # check if ctx server is same as voice server
    if ctx.guild_id != ctx.author.voice.channel.guild.id:
        return await ctx.send("I am not in the same server as you")

    if not ctx.voice_state:
        await ctx.author.voice.channel.connect()
    else:
        await ctx.voice_state.move(ctx.author.voice.channel)

    # Start recording
    await ctx.voice_state.start_recording()
    await asyncio.sleep(time)
    await ctx.voice_state.stop_recording()
    await ctx.send(files=[interactions.File(file, file_name=f"{ctx.guild.get_member(user_id).nick}.mp3"
                                            ) for user_id, file in ctx.voice_state.recorder.output.items()])


@slash_command(name="play", description="play a song!")
@slash_option(
    name="link",
    description="The song to play",
    required=True,
    opt_type=OptionType.STRING,
    autocomplete=True
)
async def play(ctx: SlashContext, *, link: str):
    await ctx.defer()
    if not ctx.voice_state:
        await ctx.author.voice.channel.connect()

    # check if link is a youtube link
    if "youtube.com" not in link:
        link = search_youtube(link)[0]['url_suffix']

    music_queue[ctx.guild_id] = music_queue.get(ctx.guild_id, [])
    music_queue[ctx.guild_id].append(MusicQueueSong(link))

    # add to queue if already playing
    if ctx.voice_state.playing:
        return await ctx.send(f"Added {link} to the queue")

    await play_next(ctx)


async def play_next(ctx: SlashContext):
    current_song = music_queue[ctx.guild_id].pop(0)

    # yt = YouTube(current_song)
    yt = current_song.yt

    # extract only audio
    video = yt.streams.filter(only_audio=True).first()
    out_file = video.download(output_path='.')

    # Get the audio using YTDL
    audio = AudioVolume(out_file)
    # create a player using embed
    embed = interactions.Embed(title="Now Playing", color=0x00ff00)
    embed.add_field(name="Title", value=yt.title, inline=False)
    embed.add_field(name="Duration", value=yt.length, inline=False)
    embed.set_thumbnail(url=yt.thumbnail_url)
    # add buttons to skip, pause, resume, stop
    await ctx.send(embed=embed, components=[
        ActionRow(
            Button(
                style=ButtonStyle.GREY,
                emoji="⏸",
                custom_id="pause"
            ),
            Button(
                style=ButtonStyle.GREY,
                emoji="⏹",
                custom_id="stop"
            ),
            Button(
                style=ButtonStyle.GREY,
                emoji="⏭",
                custom_id="skip"
            ),
        )
    ])

    await ctx.voice_state.play(audio)
    # delete the file
    os.remove(out_file)
    if len(music_queue[ctx.guild_id]) > 0:
        await play_next(ctx)


@slash_command(name="skip", description="Skip the current song")
async def skip(ctx: SlashContext):
    await ctx.send("Skipping the current song")
    await skip_current(ctx)


async def skip_current(ctx: SlashContext):
    if not ctx.voice_state:
        return await ctx.send('Not connected to any voice channel')
    if len(music_queue[ctx.guild_id]) == 0:
        return await ctx.send('No songs in queue')
    await ctx.voice_state.stop()


@play.autocomplete("link")
async def autocomplete(ctx: AutocompleteContext):
    string_option_input = ctx.input_text
    if not string_option_input or len(string_option_input) < 3:
        return await ctx.send(choices=[])
    results = search_youtube(string_option_input)
    choices = []
    for result in results:
        choices.append({
            "name": result['title'],
            "value": 'https://www.youtube.com'+result['url_suffix']
        })
    await ctx.send(
        choices=choices
    )


def search_youtube(query):
    return YoutubeSearch(query, max_results=5).to_dict()


@slash_command(name="queue", description="Show the current queue")
async def queue(ctx: SlashContext):
    if len(music_queue[ctx.guild_id]) == 0:
        return await ctx.send('No songs in queue')
    # create an embed with the current queue
    embed = interactions.Embed(title="Queue", color=0x00ff00)
    for i, song in enumerate(music_queue[ctx.guild_id]):
        embed.add_field(name=f"{i+1}. {song.yt.title}",
                        value=song.yt.length, inline=False)
    await ctx.send(embed=embed)


@slash_command(name="stop", description="Stop the audio")
async def stop(ctx=SlashContext):
    await ctx.defer()
    await stop_audio(ctx)


async def stop_audio(ctx: SlashContext):
    if not ctx.voice_state:
        return await ctx.send('Not connected to any voice channel')
    await ctx.voice_state.stop()
    # clear the queue
    music_queue[ctx.guild_id] = []
    await ctx.send('Stopped the audio')


@slash_command(name="dancing_ujju", description="Dancing Ujju")
async def dancing_ujju(ctx=SlashContext):
    # send file and automatically play the video
    await ctx.send(file=interactions.File('assets/ujju.mp4'))


@slash_command(name="pause", description="Pause the audio")
async def pause(ctx=SlashContext):
    await ctx.defer()
    await pause_audio(ctx)
    await ctx.send('Paused the audio')


async def pause_audio(ctx: SlashContext):
    if not ctx.voice_state:
        return await ctx.send('Not connected to any voice channel')
    ctx.voice_state.pause()


@slash_command(name="resume", description="Resume the audio")
async def resume(ctx=SlashContext):
    await ctx.defer()
    await resume_audio(ctx)
    await ctx.send('Resumed the audio')


async def resume_audio(ctx: SlashContext):
    if not ctx.voice_state:
        return await ctx.send('Not connected to any voice channel')
    ctx.voice_state.resume()


@slash_command(name="volume", description="Change the volume")
@slash_option(
    name="volume",
    description="The volume to set",
    required=True,
    opt_type=OptionType.INTEGER
)
async def volume(ctx=SlashContext, *, volume: int):
    await ctx.defer()
    if not ctx.voice_state:
        return await ctx.send('Not connected to any voice channel')
    ctx.voice_state.volume = volume / 100
    await ctx.send(f"Volume set to {volume}%")


@slash_command(name="play_file", description="Play an audio file")
@slash_option(
    name="file",
    description="Upload file to play",
    required=True,
    opt_type=OptionType.ATTACHMENT
)
async def play_file(ctx=SlashContext, *, file: discord.Attachment):
    await ctx.defer()
    if (not ctx.author.voice):
        return await ctx.channel.send('Join a channel first')
    if not ctx.voice_state:
        await ctx.author.voice.channel.connect()
    else:
        await ctx.voice_state.move(ctx.author.voice.channel)
    print("Playing "+file.url)
    audio = AudioVolume(file.url)
    await ctx.send("Playing "+file.url)
    await ctx.voice_state.play(audio)


@slash_command(name="porn", description="Porn PSA")
@slash_option(
    name="user",
    description="The user to send the PSA to",
    opt_type=OptionType.MENTIONABLE,
    required=True
)
async def porn(ctx=SlashContext, *, user: discord.Member):
    await ctx.send("Dear " + user.mention)
    # Original message
    message = "I am writing to you today to express my concern about your excessive consumption of pornography. While it is understandable that you may find watching porn to be a source of pleasure, it is important to understand that this behavior can have serious consequences on your mental health and relationships.\nResearch has shown that consuming too much pornography can lead to addiction, decreased sexual satisfaction, and a distorted perception of sexuality. It can also lead to feelings of guilt, shame, and anxiety, and may even contribute to the development of depression.\nFurthermore, excessive porn use can negatively impact your relationships with friends and family, and may even lead to problems in your romantic relationships. It is important to prioritize healthy communication and connection with those around you, rather than relying solely on the instant gratification of pornography.\nI urge you to consider the potential consequences of your behavior and to seek help if you feel that you are struggling to control your consumption of pornography. There are resources available to support you in overcoming this addiction and developing healthier habits.\nSincerely,\nChatGPT\n"
    # automatically split the message into multiple messages based on 2000 character limit
    while len(message) > 0:
        await ctx.send(message[:2000])
        message = message[2000:]


@slash_command(name="remove_bg", description="Remove the background")
@slash_option(
    name="user",
    description="The user to remove the background for",
    opt_type=OptionType.USER,
    required=True
)
async def remove_bg(ctx=SlashContext, *, user: discord.Member):
    await ctx.defer()
    # User remove.bg to remove the background
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
        await ctx.send(user.mention)
        await ctx.send(file='temp.png')


@slash_command(name="weather", description="Get the weather")
@slash_option(
    name="city",
    description="The city to get weather for",
    opt_type=OptionType.STRING,
    required=True
)
async def weather(ctx=SlashContext, *, city: str):
    await ctx.defer()
    response = requests.get(
        'http://api.openweathermap.org/data/2.5/weather?q='+city+'&appid='+OPEN_WEATHER_KEY)
    data = response.json()
    embed = interactions.Embed(title="Weather", color=0x00ff00)
    embed.set_author(name=ctx.author.username, icon_url=ctx.author.avatar_url)
    embed.add_field(name="City", value=city, inline=False)
    embed.add_field(name="Temperature", value=str(
        round(data['main']['temp']-273.15))+"°C", inline=False)
    embed.add_field(name="Description",
                    value=data['weather'][0]['description'], inline=False)
    await ctx.send(embed=embed)


@slash_command(name="remind_me", description="Remind you about something after a certain time")
@slash_option(
    name="time",
    description="The time to remind you",
    opt_type=OptionType.INTEGER,
    required=True
)
@slash_option(
    name="message",
    description="The message to remind you about",
    opt_type=OptionType.STRING,
    required=True
)
async def remind_me(ctx=SlashContext, *, time: str, message: str):
    await ctx.defer()
    embed = interactions.Embed(title="Reminder", color=0x00ff00)
    embed.set_author(name=ctx.author.username, icon_url=ctx.author.avatar_url)
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


@slash_command(name="drops", description="Check for streams that have drops enabled for a game")
@slash_option(
    name="game",
    description="The game to get drops for",
    opt_type=OptionType.STRING,
    required=True
)
async def drops(ctx=SlashContext, *, game: str):
    embed = interactions.Embed(title=game+" streams", color=0x00ff00)
    await ctx.defer()
    for stream in getStreams(game):
        if (stream['node']['broadcaster']):
            embed.add_field(name=stream['node']['title'], value="https://www.twitch.tv/" +
                            stream['node']['broadcaster']['login'], inline=False)
    await ctx.send(embed=embed, components=[
        ActionRow(
            Button(
                style=ButtonStyle.URL,
                label="Open in browser",
                url="https://www.twitch.tv/directory/game/" +
                    game.replace(" ", "%20") +
                "?tl=DropsEnabled"
            ),
            Button(
                style=ButtonStyle.blue,
                label="Refresh",
            )
        )
    ])

games = {'Fortnite': 1,
         'Overwatch 2': 1,
         'XDefiant': 1,
         'Valorant': 1}


def create_buttons(choice):
    buttons = [
        Button(
            style=ButtonStyle.green,
            label="Choose again",
            custom_id="choose_again",
        ),
        Button(
            style=ButtonStyle.red,
            label="Remove choice and choose again",
            custom_id="remove_choice_and_choose_again_"+choice),
    ]
    return ActionRow(*buttons)


@slash_command(name="choose", description="Choose a game")
async def choose(ctx=SlashContext):
    choice = random.choices(
        list(games.keys()), weights=list(games.values()), k=1)[0]

    await ctx.send(embed=interactions.Embed(title="I choose "+choice, color=0x00ff00), components=[create_buttons(choice)])


@ slash_command(name="games", description="Show list of games")
async def listgames(ctx=SlashContext):
    global games
    total = sum(games.values())
    embed = interactions.Embed(title="Games", color=0x00ff00)
    for game in games:
        embed.add_field(name=game, value=games[game], inline=False)
    await ctx.send(embed=embed)


@ slash_command(name="addgame", description="Add new game")
@ slash_option(
    name="game",
    description="The game to add",
    opt_type=OptionType.STRING,
    required=True
)
@ slash_option(
    name="weight",
    description="The weight of the game",
    opt_type=OptionType.INTEGER,
    required=True
)
async def addgame(ctx=SlashContext, *, game: str, weight: int):
    games[game] = weight
    total = sum(games.values())
    embed = interactions.Embed(title="Games", color=0x00ff00)
    for game in games:
        embed.add_field(name=game, value=games[game], inline=False)
    await ctx.send(embed=embed)


@ slash_command(name="removegame", description="Remove existing game")
@ slash_option(
    name="game",
    description="The game to remove",
    opt_type=OptionType.STRING,
    required=True
)
async def removegame(ctx=SlashContext, *, game: str):
    for g in games:
        if (g.lower().replace(" ", "") == game.lower().replace(" ", "")):
            del games[g]
            break
    total = sum(games.values())
    embed = interactions.Embed(title="Games", color=0x00ff00)
    for game in games:
        embed.add_field(name=game, value=games[game], inline=False)
    await ctx.send(embed=embed)


@ slash_command(name="changeweight", description="Change weight for game")
@ slash_option(
    name="game",
    description="The game to change weight for",
    opt_type=OptionType.STRING,
    required=True
)
@ slash_option(
    name="weight",
    description="The new weight of the game",
    opt_type=OptionType.INTEGER,
    required=True
)
async def changeweight(ctx=SlashContext, *, game: str, weight: int):
    for g in games:
        if (g.lower().replace(" ", "") == game.lower().replace(" ", "")):
            games[g] = weight
            break
    total = sum(games.values())
    embed = interactions.Embed(title="Games", color=0x00ff00)
    for game in games:
        embed.add_field(name=game, value=games[game], inline=False)
    await ctx.send(embed=embed)


@listen(Component)
async def on_component(event: Component):
    ctx = event.ctx
    if ctx.custom_id == "choose_again":
        await choose(ctx)
    elif "remove_choice_and_choose_again" in ctx.custom_id:
        choice = ctx.custom_id.split("_")[-1]
        for g in games:
            if (g.lower().replace(" ", "") == choice.lower().replace(" ", "")):
                del games[g]
                break
        await choose(ctx)
    elif ctx.custom_id == "pause":
        # change icon and label to resume
        await pause_audio(ctx)
        await ctx.edit_origin(components=[
            ActionRow(
                Button(
                    style=ButtonStyle.green,
                    emoji="▶️",
                    custom_id="resume"
                ),
                Button(
                    style=ButtonStyle.grey,
                    emoji="⏹",
                    custom_id="stop"
                ),
                Button(
                    style=ButtonStyle.grey,
                    emoji="⏭",
                    custom_id="skip"
                ),
            )
        ])
    elif ctx.custom_id == "resume":
        await resume_audio(ctx)
        await ctx.edit_origin(components=[
            ActionRow(
                Button(
                    style=ButtonStyle.grey,
                    emoji="⏸",
                    custom_id="pause"
                ),
                Button(
                    style=ButtonStyle.grey,
                    emoji="⏹",
                    custom_id="stop"
                ),
                Button(
                    style=ButtonStyle.grey,
                    emoji="⏭",
                    custom_id="skip"
                ),
            )
        ])
    elif ctx.custom_id == "stop":
        return await stop_audio(ctx)
    elif ctx.custom_id == "skip":
        return await skip_current(ctx)
    elif ctx.custom_id.startswith("soundboard_sound_"):
        id = ctx.custom_id.replace("soundboard_sound_", "")
        sound = soundboardCollection.find_one({"_id": id})
        print("Playing sound "+sound['name'])
        await ctx.defer()
        await playUrl(ctx, sound['sound'])
        await ctx.send("Playing "+sound['name'])


async def playUrl(ctx, url):
    # join the voice channel and play the audio
    if not ctx.voice_state:
        await ctx.author.voice.channel.connect()
    else:
        await ctx.voice_state.move(ctx.author.voice.channel)
    audio = AudioVolume(url)
    await ctx.voice_state.play(audio)


@ slash_command(name="rlrank", description="Get ranks for rocket league")
@ slash_option(
    name="epicid",
    description="The epic id",
    opt_type=OptionType.STRING,
    required=True
)
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
    data = data.json()
    ranks = []
    for mode in data['data']['segments']:
        if (mode['type'] == "playlist"):
            ranks.append([mode['metadata']['name'],
                          mode['stats']['tier']['metadata']['name'],
                          mode['stats']['division']['metadata']['name'],
                          mode['stats']['tier']['value'],
                          mode['stats']['tier']['metadata']['iconUrl']])
    highest = ranks[0]
    for rank in ranks:
        if (rank[3] > highest[3]):
            highest = rank
    embed = interactions.Embed(title="Ranks for "+epicid, color=0x00ff00)
    for rank in ranks:
        embed.add_field(name=rank[0], value=rank[1]+" "+rank[2], inline=False)
    embed.set_thumbnail(url=highest[4])
    await ctx.send(embed=embed)

palm.configure(api_key=os.getenv("PALM_API_KEY"))


@slash_command(name="writecode", description="Write code for you")
@slash_option(
    name="message",
    description="The message to write code for",
    opt_type=OptionType.STRING,
    required=True
)
async def writecode(ctx=SlashContext, *, message: str):
    await ctx.defer()
    defaults = {
        'model': 'models/chat-bison-001',
        'temperature': 0.25,
        'candidate_count': 1,
        'top_k': 40,
        'top_p': 0.95,
    }
    context = "Write Python code to perform what the user says."
    examples = [
        [
            "Write a function to add 2 numbers",
            "Sure, here is the function to add 2 numbers:\n```python\ndef sum(a,b):\n    return a+b\n```"
        ],
        [
            "Write a function to find the factorial of a number",
            "Sure, here is the function to find the factorial of a number:\n```python\ndef factorial(n):\n    if n == 0:\n        return 1\n    else:\n        return n * factorial(n-1)\n```"
        ]
    ]
    messages = [
        "Write a function to reverse a list",
        "Sure, here is a function to reverse a list:\n\n```python\ndef reverse_list(list):\n    # Create a new list to store the reversed list\n    reversed_list = []\n\n    # Iterate through the original list\n    for item in list:\n        # Add the item to the end of the reversed list\n        reversed_list.append(item)\n\n    # Return the reversed list\n    return reversed_list\n```\n\nHere is an example of how to use the function:\n\n```python\nlist = [1, 2, 3, 4, 5]\nreversed_list = reverse_list(list)\nprint(reversed_list)\n```\n\nThe output of the code is:\n\n```\n[5, 4, 3, 2, 1]\n```"
    ]
    messages.append(message)
    response = palm.chat(
        **defaults,
        context=context,
        examples=examples,
        messages=messages
    )
    # split it out into chunks of 2000 characters and send them
    output = response.last
    while len(output) > 2000:
        await ctx.send(output[:2000])
        output = output[2000:]
    await ctx.send(output)


@slash_command(name="solutionsguy", description="Modify your code")
@slash_option(
    name="message",
    description="The message to modify",
    opt_type=OptionType.STRING,
    required=True
)
async def solutionsguy(ctx=SlashContext, *, message: str):
    await ctx.defer()
    defaults = {
        'model': 'models/chat-bison-001',
        'temperature': 0.25,
        'candidate_count': 1,
        'top_k': 40,
        'top_p': 0.95,
    }
    context = "Rewrite this Python code such that the logic remains the same but the code looks completely different."
    examples = [
        [
            "def sum(a,b):\n    return a + b",
            "Sure, here is a different way to write the code:\n```python\nsum = lambda a, b: a + b\n```"
        ],
        [
            "def sum(a,b):\n    return a + b",
            "Sure, here is a different way to write the code:\n```python\ndef add(num1,num2):\n    total=num1+num2\n    return total\n```"
        ],
        [
            "for i in range(1, 11):\n    print(i)",
            "Sure, here is a different way to write the code:\n```python\ni = 1\nwhile(i<=10):\n    print(i)\n    i += 1\n```"
        ],
        [
            "import json\n\nwith open('file.json', 'r') as f:\n  data = json.load(f)",
            "Sure, here is a different way to write the code:\n```python\nimport ast\n\nfile_path = 'file.json'\n\nwith open(file_path, 'r') as file:\n    contents = file.read()\n    data = ast.literal_eval(contents)\n```"
        ],
        [
            "print(\"Hello World\")",
            "Sure, here is a different way to write the code:\n```python\noutput=\"Hello World\"\nprint(output)\n```"
        ]
    ]
    messages = []
    messages.append(message)
    response = palm.chat(
        **defaults,
        context=context,
        examples=examples,
        messages=messages
    )
    await ctx.send(response.last)


@ slash_command(name="atharavsolutions", description="Helps you cheat on your homework")
async def chat(ctx=SlashContext):
    test = """Attention all computer science students! Are you tired of losing grades and facing academic penalties for copying code? If yes, Atharav Solutions is here to help you.

Atharav, a student just like you, faced the same problem and failed a class three times due to his inability to copy code effectively. He was determined to find a solution and created Atharav Solutions to help other students like him.

Our company specializes in changing code so it does not look like copied code, helping you avoid plagiarism penalties. With Atharav Solutions, you can say goodbye to lost grades and hello to successful academic careers.

Our team of experts uses innovative techniques to ensure that your code is unique and original, and that you receive the grades you deserve. With Atharav Solutions, you can focus on learning and growing in your computer science studies without the fear of academic penalties.

So, if you're ready to put an end to the problem of copying code and succeed in your computer science classes, choose Atharav Solutions. Contact us today and let us help you achieve your academic goals!

https://atharav-solutions.onrender.com/
    """
    await ctx.send(test)


@ slash_command(name="githubstats", description="Get github stats")
@ slash_option(
    name="username",
    description="The username to get stats for",
    opt_type=OptionType.STRING,
    required=True
)
async def githubstats(ctx=SlashContext, *, username: str):
    await ctx.send("Hmmm...")
    data = requests.get("https://api.github.com/users/"+username)
    data = data.json()
    embed = interactions.Embed(
        title="Github stats for "+username, color=0x00ff00)
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


@ slash_command(name="help", description="View all of the commands")
async def help(ctx=SlashContext):
    # dynamically create the embed
    embed = interactions.Embed(title="Help", color=0x00ff00)
    for command in bot.application_commands:
        embed.add_field(name="/"+str(command.name),
                        value=str(command.description), inline=False)
    await ctx.send(embed=embed)


@ slash_command(name="dc", description="Disconnet a user from the voice channel")
@ slash_option(
    name="user",
    description="The user to disconnect",
    opt_type=OptionType.USER,
    required=True
)
async def dc(ctx=SlashContext, *, user: discord.Member):
    if not ctx.author.has_permission(interactions.Permissions.MOVE_MEMBERS):
        await ctx.send("You do not have permission to move members")
        return
    try:
        await user.move(None)
        await ctx.send("Disconnected "+user.mention)
    except Exception as e:
        await ctx.send("Error: "+str(e))


@ slash_command(name="move", description="Move a user to a voice channel")
@ slash_option(
    name="user",
    description="The user to move",
    opt_type=OptionType.USER,
    required=True
)
@ slash_option(
    name="channel",
    description="The channel to move the user to",
    opt_type=OptionType.CHANNEL,
    required=True
)
async def move(ctx=SlashContext, *, user: discord.Member, channel: interactions.GuildVoice):
    if not ctx.author.has_permission(interactions.Permissions.MOVE_MEMBERS):
        await ctx.send("You do not have permission to move members")
        return
    try:
        await user.move(channel.id)
        await ctx.send("Moved "+user.mention+" to #"+channel.name)
    except Exception as e:
        await ctx.send("Error: "+str(e))


async def move_randomly():
    guild = bot.get_guild(1030766503949254656)
    channels = await guild.fetch_channels()
    channels = [channel for channel in channels if isinstance(
        channel, interactions.GuildVoice)]
    # find channel with most members
    max_members = 0
    channel_with_most_members = channels[0]
    for channel in channels:
        if (len(channel.voice_members) > max_members):
            max_members = len(channel.voice_members)
            channel_with_most_members = channel
    members = channel_with_most_members.voice_members
    if (len(members) > 0):
        # move a random member to a random channel
        channels.remove(channel_with_most_members)
        member = random.choice(members)
        new_channel = random.choice(channels)
        await member.move(new_channel.id)
        return member, new_channel
    return None, None


@slash_command(name="random_move", description="Randomly move a user to a voice channel")
async def random_move(ctx=SlashContext):
    # check if user has move permissions
    if not ctx.author.has_permission(interactions.Permissions.MOVE_MEMBERS):
        await ctx.send("You do not have permission to move members")
        return
    member, new_channel = await move_randomly()
    if member:
        await ctx.send("Moved "+member.username+" to "+new_channel.name)
    else:
        await ctx.send("No one to move")


@slash_command(name="start_auto_move", description="Start moving users randomly every 10 minutes")
async def start_auto_move(ctx=SlashContext):
    global auto_move_randomly
    auto_move_randomly.start()
    await ctx.send("Started moving users randomly every 10 minutes")


@slash_command(name="stop_auto_move", description="Stop moving users randomly every 10 minutes")
async def stop_auto_move(ctx=SlashContext):
    global auto_move_randomly
    auto_move_randomly.stop()
    await ctx.send("Stopped moving users randomly every 10 minutes")


@slash_command(name="mention_roles", description="Mention all for a user")
@slash_option(
    name="user",
    description="The user to mention",
    opt_type=OptionType.USER,
    required=True
)
async def mention_roles(ctx=SlashContext, *, user: discord.Member):
    roles = user.roles
    roles = [role.mention for role in roles]
    await ctx.send(" ".join(roles))


@slash_command(name="generate_meme", description="Generate a meme using a image and text")
@slash_option(
    name="image",
    description="The image to use for the meme",
    opt_type=OptionType.ATTACHMENT,
    required=True
)
@slash_option(
    name="text",
    description="The text to use for the meme",
    opt_type=OptionType.STRING,
    required=True
)
async def generate_meme(ctx=SlashContext, *, image: discord.Attachment, text: str):
    await ctx.defer()
    lines = textwrap.wrap(text, 30)
    text = "\n".join(lines)

    img = Image.open(requests.get(image.url, stream=True).raw)

    box = ((0, 0, img.width, int(0.25*img.height)))

    font_size = 500
    size = None
    draw_test = ImageDraw.Draw(img)
    while (size is None or size[0] > box[2] - box[0] or size[1] > box[3] - box[1]) and font_size > 0:
        font = ImageFont.load_default(size=font_size)
        left, top, right, bottom = draw_test.multiline_textbbox(
            (0, 0), text, font)
        size = [right - left, bottom - top]
        font_size -= 1

    new_size = (img.width, int(1.25*img.height))

    new = Image.new('RGBA', new_size, (0, 0, 0, 0))
    new.paste(img, (0, int(0.25*img.height)))
    draw_new = ImageDraw.Draw(new)
    draw_new.rectangle(box, fill=(255, 255, 255))

    draw_new.multiline_text((box[0], box[1]), text, "black", font)

    new.save('temp.png')
    await ctx.send(file='temp.png')


def saveSoundLocally(name, url):
    if not os.path.exists("sounds"):
        os.makedirs("sounds")
    print("Saving sound "+name, url)
    res = requests.get(url)
    with open("sounds/"+name, "wb") as f:
        f.write(res.content)


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
async def add_sound(ctx=SlashContext, *, name: str, emoji: str, sound: discord.Attachment):
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
    soundboardCollection.update_one(
        {"_id": soundId}, soundboardRow, upsert=True)
    await ctx.send("Added sound "+name)


@slash_command(name="remove_sound", description="Remove a sound from the soundboard")
@slash_option(
    name="name",
    description="The name of the sound to remove",
    opt_type=OptionType.STRING,
    required=True
)
async def remove_sound(ctx=SlashContext, *, name: str):
    await ctx.defer()
    soundId = name.lower()+"_"+str(ctx.guild_id)
    soundboardCollection.delete_one({"_id": soundId})
    await ctx.send("Removed sound "+name)


@slash_command(name="soundboard", description="Play a sound from the soundboard")
async def soundboard(ctx=SlashContext, *, name: str = None):
    # get all sounds for the server
    sounds = soundboardCollection.find(
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
    embed = interactions.Embed(title="Soundboard", color=0x00ff00,
                               description="Choose a sound to play")
    await ctx.send(embed=embed, components=[ActionRow(*buttons)])


@listen(CommandError, disable_default_listeners=True)
async def on_command_error(event: CommandError):
    traceback.print_exception(event.error)
    if not event.ctx.responded:
        await event.ctx.send("Something went wrong.")


@ tasks.loop(minutes=1)
async def auto_move_randomly():
    # 10% chance of moving a user every 10 minutes
    if (random.random() < 1):
        member, new_channel = await move_randomly()
        if member:
            print("Moved "+member.name+" to "+new_channel.name)
        else:
            print("No one to move")


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
    global cloned_voices

    url = "https://api.play.ht/api/v2/cloned-voices"
    headers = {
        "AUTHORIZATION": PLAY_HT_KEY,
        "X-USER-ID": PLAY_HT_APP_ID
    }
    response = requests.get(url, headers=headers)
    cloned_voices = response.json()

print("Running bot")

bot.start(TOKEN)
