import discord
from discord import app_commands
from discord.ext import commands
import requests

class HashiruCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.current_voice_channel = {}
        self.deafened_channel = {1030766503949254656:1297374877656940587}
    
    @app_commands.command(name="update_deafened_channel", description="Update the channel to move deafened users to")
    @app_commands.describe(
        channel="The channel to move deafened users to",
    )
    async def update_deafened_channel(self, inter: discord.Interaction, channel: discord.VoiceChannel):
        self.deafened_channel[inter.guild.id] = channel.id
        await inter.response.send_message("Updated the channel to move deafened users")
    
    @app_commands.command(name="chat", description="Chat with LLMs")
    @app_commands.describe(
        prompt="The prompt to send to the LLM",
    )
    async def chat(self, inter: discord.Interaction, prompt: str):
        await inter.response.defer()
        url = "http://10.0.0.40:5678/webhook/aa1cd686-c3a6-41b7-b274-b9fce73b40fa"
        body = {
            "prompt": prompt,
            "server": inter.guild.id,
            "user": inter.user.name
        }
        response = requests.post(url, json=body)
        if response.status_code == 200:
            data = response.json()
            if data.get("error"):
                await inter.followup.send(f"Error: {data['error']}")
            else:
                # split into chunks of 2000 characters
                response_text = data.get("response", "")
                chunks = [response_text[i:i + 2000] for i in range(0, len(response_text), 2000)]
                for chunk in chunks:
                    if len(chunk) > 0:
                        await inter.followup.send(chunk)
        else:
            await inter.followup.send("Error: Unable to connect to the LLM server")
    
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if not member.voice:
            return
        if member.voice.self_deaf:
            guild = member.guild
            voice_channel = guild.get_channel(self.deafened_channel.get(guild.id))
            # check if already in the channel
            if member.voice.channel == voice_channel:
                return
            if voice_channel is None:
                return
            self.current_voice_channel[member.id] = member.voice.channel
            
            await member.move_to(voice_channel)
        else:
            if member.id in self.current_voice_channel:
                await member.move_to(self.current_voice_channel[member.id])
                self.current_voice_channel.pop(member.id, None)

async def setup(bot):
    print("Adding HashiruCommands")
    await bot.add_cog(HashiruCommands(bot))


async def teardown(bot):
    print("Unloaded HashiruCommands")