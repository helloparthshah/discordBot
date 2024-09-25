import discord
from interactions import Extension, OptionType, slash_command, slash_option
import os
from dotenv import load_dotenv
from interactions import SlashContext
import requests
from io import BytesIO
from PIL import Image, ImageFont, ImageDraw
import textwrap


class ImageUtils(Extension):
    def __init__(self, bot):
        load_dotenv()
        self.REMOVE_BG_KEY = os.getenv('REMOVE_BG_KEY')

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
    async def generate_meme(self, ctx=SlashContext, *, image: discord.Attachment, text: str):
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

    @slash_command(name="remove_bg", description="Remove the background")
    @slash_option(
        name="image",
        description="The image to remove the background from",
        opt_type=OptionType.ATTACHMENT,
        required=True
    )
    async def remove_bg(self, ctx=SlashContext, *, image: discord.Attachment):
        await ctx.defer()
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
            await ctx.send(file='temp.png')


def setup(bot):
    ImageUtils(bot)
