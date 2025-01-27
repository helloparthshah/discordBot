import discord
import os
from dotenv import load_dotenv
import requests
from io import BytesIO
from PIL import Image, ImageFont, ImageDraw
import textwrap
import discord
from discord import app_commands
from discord.ext import commands


class ImageUtils(commands.Cog):
    def __init__(self, bot):
        load_dotenv()
        self.bot = bot
        self.REMOVE_BG_KEY = os.getenv('REMOVE_BG_KEY')

    @app_commands.command(name="generate_meme", description="Generate a meme using a image and text")
    @app_commands.describe(
        image="The image to use for the meme",
        text="The text to use for the meme",
    )
    async def generate_meme(self, inter: discord.Interaction, image: discord.Attachment, text: str):
        await inter.response.defer()
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
        await inter.followup.send(file=discord.File('temp.png'))

    @app_commands.command(name="remove_bg", description="Remove the background")
    @app_commands.describe(
        image="The image to remove the background from",
    )
    async def remove_bg(self, inter: discord.Interaction, image: discord.Attachment):
        await inter.response.defer()
        # User remove.bg to remove the background
        image = Image.open(BytesIO(requests.get(image.url).content))
        image.save('temp.png')
        response = requests.post(
            'https://api.remove.bg/v1.0/removebg',
            files={'image_file': open('./temp.png', 'rb')},
            data={'size': 'auto'},
            headers={'X-Api-Key': self.REMOVE_BG_KEY},
        )
        if response.status_code == requests.codes.ok:
            with open('temp.png', 'wb') as out:
                out.write(response.content)
            await inter.followup.send(file=discord.File('temp.png'))

async def setup(bot):
    print("Adding ImageUtils")
    await bot.add_cog(ImageUtils(bot))


async def teardown(bot):
    print("Unloaded ImageUtils")
