import asyncio
import discord
from interactions import Embed, Extension, File, OptionType, slash_command, slash_option
import os
from dotenv import load_dotenv
from interactions import SlashContext
import requests


class Extras(Extension):
    def __init__(self, bot):
        load_dotenv()
        self.OPEN_WEATHER_KEY = os.getenv('OPEN_WEATHER_KEY')

    @slash_command(name="yo_mama", description="Yo mama")
    @slash_option(
        name="user",
        description="The user to send the joke to",
        opt_type=OptionType.USER,
        required=True
    )
    async def yo_mama(self, ctx=SlashContext, *, user: discord.Member):
        await ctx.defer()
        # read the response and send it as a message
        response = requests.get(
            'https://www.yomama-jokes.com/api/v1/jokes/random/')
        data = response.json()
        print(data)
        await ctx.send(user.mention)
        await ctx.send(embed=Embed(
            title=data['joke'], color=0x00ff00))

    @slash_command(name="dancing_ujju", description="Dancing Ujju")
    async def dancing_ujju(self, ctx=SlashContext):
        # send file and automatically play the video
        await ctx.send(file=File('assets/ujju.mp4'))

    @slash_command(name="porn", description="Porn PSA")
    @slash_option(
        name="user",
        description="The user to send the PSA to",
        opt_type=OptionType.MENTIONABLE,
        required=True
    )
    async def porn(self, ctx=SlashContext, *, user: discord.Member):
        await ctx.send("Dear " + user.mention)
        # Original message
        message = "I am writing to you today to express my concern about your excessive consumption of pornography. While it is understandable that you may find watching porn to be a source of pleasure, it is important to understand that this behavior can have serious consequences on your mental health and relationships.\nResearch has shown that consuming too much pornography can lead to addiction, decreased sexual satisfaction, and a distorted perception of sexuality. It can also lead to feelings of guilt, shame, and anxiety, and may even contribute to the development of depression.\nFurthermore, excessive porn use can negatively impact your relationships with friends and family, and may even lead to problems in your romantic relationships. It is important to prioritize healthy communication and connection with those around you, rather than relying solely on the instant gratification of pornography.\nI urge you to consider the potential consequences of your behavior and to seek help if you feel that you are struggling to control your consumption of pornography. There are resources available to support you in overcoming this addiction and developing healthier habits.\nSincerely,\nChatGPT\n"
        # automatically split the message into multiple messages based on 2000 character limit
        while len(message) > 0:
            await ctx.send(message[:2000])
            message = message[2000:]

    @slash_command(name="weather", description="Get the weather")
    @slash_option(
        name="city",
        description="The city to get weather for",
        opt_type=OptionType.STRING,
        required=True
    )
    async def weather(self, ctx=SlashContext, *, city: str):
        await ctx.defer()
        response = requests.get(
            'http://api.openweathermap.org/data/2.5/weather?q='+city+'&appid='+self.OPEN_WEATHER_KEY)
        data = response.json()
        embed = Embed(title="Weather", color=0x00ff00)
        embed.set_author(name=ctx.author.username,
                         icon_url=ctx.author.avatar_url)
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
    async def remind_me(self, ctx=SlashContext, *, time: str, message: str):
        await ctx.defer()
        embed = Embed(title="Reminder", color=0x00ff00)
        embed.set_author(name=ctx.author.username,
                         icon_url=ctx.author.avatar_url)
        embed.add_field(name="Message", value=message, inline=False)
        embed.add_field(name="Time", value=time, inline=False)
        await ctx.send(embed=embed)
        await asyncio.sleep(int(time))
        await ctx.send(ctx.author.mention)
        await ctx.send(message)

    @slash_command(name="mention_roles", description="Mention all for a user")
    @slash_option(
        name="user",
        description="The user to mention",
        opt_type=OptionType.USER,
        required=True
    )
    async def mention_roles(self, ctx=SlashContext, *, user: discord.Member):
        roles = user.roles
        roles = [role.mention for role in roles]
        await ctx.send(" ".join(roles))

    @slash_command(name="githubstats", description="Get github stats")
    @slash_option(
        name="username",
        description="The username to get stats for",
        opt_type=OptionType.STRING,
        required=True
    )
    async def githubstats(self, ctx=SlashContext, *, username: str):
        await ctx.defer()
        data = requests.get("https://api.github.com/users/"+username)
        data = data.json()
        embed = Embed(
            title="Github stats for "+username, color=0x00ff00)
        embed.add_field(name="Followers",
                        value=data['followers'], inline=False)
        embed.add_field(name="Following",
                        value=data['following'], inline=False)
        embed.add_field(name="Public repos",
                        value=data['public_repos'], inline=False)
        embed.add_field(name="Public gists",
                        value=data['public_gists'], inline=False)
        embed.add_field(name="Bio", value=data['bio'], inline=False)
        embed.set_thumbnail(url=data['avatar_url'])
        await ctx.send(embed=embed)
        await ctx.send("https://streak-stats.demolab.com/?user="+username+"&theme=radical&type=png")


def setup(bot):
    Extras(bot)
