#!/usr/bin/python3
from io import BytesIO
import random
from PIL import Image
import requests
import os
import discord
from dotenv import load_dotenv
from discord import Client, Intents, Embed
import asyncio
from discord_slash import SlashCommand, SlashContext
from discord.ext import tasks
from discord_slash.utils.manage_components import create_button, create_actionrow
from discord_slash.model import ButtonStyle
import google.generativeai as palm

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
REMOVE_BG_KEY = os.getenv('REMOVE_BG_KEY')
OPEN_WEATHER_KEY = os.getenv('OPEN_WEATHER_KEY')
PALM_API_KEY = os.getenv('PALM_API_KEY')

bot = Client(intents=Intents.default())
slash = SlashCommand(bot, sync_commands=True)


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
    # Original message
    message = "I am writing to you today to express my concern about your excessive consumption of pornography. While it is understandable that you may find watching porn to be a source of pleasure, it is important to understand that this behavior can have serious consequences on your mental health and relationships.\nResearch has shown that consuming too much pornography can lead to addiction, decreased sexual satisfaction, and a distorted perception of sexuality. It can also lead to feelings of guilt, shame, and anxiety, and may even contribute to the development of depression.\nFurthermore, excessive porn use can negatively impact your relationships with friends and family, and may even lead to problems in your romantic relationships. It is important to prioritize healthy communication and connection with those around you, rather than relying solely on the instant gratification of pornography.\nI urge you to consider the potential consequences of your behavior and to seek help if you feel that you are struggling to control your consumption of pornography. There are resources available to support you in overcoming this addiction and developing healthier habits.\nSincerely,\nChatGPT\n"
    await ctx.send(message)
    message = "However, one important aspect of my research as a pornography historian is the study of different pornography categories and how they have evolved over time. For example, the emergence of amateur pornography in the 20th century represents a significant shift in the production and consumption of pornography. Amateur pornography challenged the dominance of mainstream, professionally-produced pornography and provided a space for individuals to create and share their own sexual content. This category of pornography has since become increasingly popular, and has even led to the emergence of user-generated content platforms such as OnlyFans.\n Another important category of pornography that I have researched is feminist pornography. This category of pornography emerged in the 1980s as a response to the male-dominated and often exploitative nature of mainstream pornography. Feminist pornography aimed to challenge gender stereotypes and promote sexual agency and empowerment for women. By studying feminist pornography, we can better understand the ways in which pornography has been used to challenge power dynamics and promote social change.\nA third category of pornography that I have researched is pornography that features people of color. Historically, mainstream pornography has been overwhelmingly dominated by white performers, perpetuating racial stereotypes and contributing to the marginalization of people of color. By examining the history of pornography that features people of color, we can better understand the ways in which race and ethnicity have been depicted and negotiated in pornography, and the impact this has had on broader social and cultural attitudes towards race and sexuality.\nOverall, the study of different pornography categories is important because it helps us to understand the diversity of sexual expression and representation, and sheds light on the complex ways in which pornography intersects with broader social and cultural issues such as gender, race, and power."
    await ctx.send(message)


@slash.slash(name="rickroll", description="Never gonna give you up")
async def rickroll(ctx=SlashContext, *, link: str, user: discord.Member):
    await ctx.send(user.mention)
    await ctx.send("https://www.latlmes.com/breaking/"+link.replace(" ", "-"))


@slash.slash(name="remove_bg", description="Remove the background")
async def remove_bg(ctx=SlashContext, *, user: discord.Member):
    # User remove.bg to remove the background
    await ctx.send(user.mention)
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

palm.configure(api_key=os.getenv("PALM_API_KEY"))


@slash.slash(name="writecode", description="Write code for you")
async def writecode(ctx=SlashContext, *, message: str):
    await ctx.send("Hmmm...")
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
        ],
    ]
    messages = []
    messages.append("NEXT REQUEST")
    response = palm.chat(
        **defaults,
        context=context,
        examples=examples,
        messages=messages
    )
    await ctx.send(response.last)


@ slash.slash(name="solutionsguy", description="Modify your code")
async def chat(ctx=SlashContext, *, message: str):
    defaults = {
        'model': 'models/chat-bison-001',
        'temperature': 0.25,
        'candidate_count': 1,
        'top_k': 40,
        'top_p': 0.95,
    }
    await ctx.send("Hmmm...")
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
