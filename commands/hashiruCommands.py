import discord
from discord import app_commands
from discord.ext import commands

class HashiruCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.current_voice_channel = {}
        self.deafened_channel = 1297374877656940587
    
    @app_commands.command(name="update_deafened_channel", description="Update the channel to move deafened users to")
    @app_commands.describe(
        channel="The channel to move deafened users to",
    )
    async def update_deafened_channel(self, inter: discord.Interaction, channel: discord.VoiceChannel):
        self.deafened_channel = channel.id
        await inter.response.send_message("Updated the channel to move deafened users")
    
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.voice.self_deaf or member.voice.self_mute:
            guild = member.guild
            voice_channel = guild.get_channel(self.deafened_channel)
            # check if already in the channel
            if member.voice.channel == voice_channel:
                return
            self.current_voice_channel[member.id] = member.voice.channel
            
            await member.move_to(voice_channel)
        else:
            if member.id in self.current_voice_channel:
                await member.move_to(self.current_voice_channel[member.id])
                self.current_voice_channel.pop(member.id, None)

async def setup(bot):
    print("Adding MusicCommands")
    await bot.add_cog(HashiruCommands(bot))


async def teardown(bot):
    print("Unloaded MusicCommands")