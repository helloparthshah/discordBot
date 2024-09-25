import random
from interactions import ActionRow, Button, ButtonStyle, Embed, Extension, OptionType, listen, slash_command, slash_option
from interactions import SlashContext
from interactions.api.events import Component


class GameChooser(Extension):
    def __init__(self, bot):
        self.games = {'Fortnite': 1,
                      'Valorant': 1}

    def create_buttons(self, choice):
        buttons = [
            Button(
                style=ButtonStyle.GREEN,
                label="Choose again",
                custom_id="choose_again",
            ),
            Button(
                style=ButtonStyle.RED,
                label="Remove choice and choose again",
                custom_id="remove_choice_and_choose_again_"+choice),
        ]
        return ActionRow(*buttons)

    @slash_command(name="choose", description="Choose a game")
    async def choose(self, ctx=SlashContext):
        choice = random.choices(
            list(self.games.keys()), weights=list(self.games.values()), k=1)[0]

        await ctx.send(embed=Embed(title="I choose "+choice, color=0x00ff00), components=[self.create_buttons(choice)])

    @slash_command(name="games", description="Show list of games")
    async def listgames(self, ctx=SlashContext):
        embed = Embed(title="Games", color=0x00ff00)
        for game in self.games:
            embed.add_field(name=game, value=self.games[game], inline=False)
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
    async def addgame(self, ctx=SlashContext, *, game: str, weight: int):
        self.games[game] = weight
        embed = Embed(title="Games", color=0x00ff00)
        for game in self.games:
            embed.add_field(name=game, value=self.games[game], inline=False)
        await ctx.send(embed=embed)

    @ slash_command(name="removegame", description="Remove existing game")
    @ slash_option(
        name="game",
        description="The game to remove",
        opt_type=OptionType.STRING,
        required=True
    )
    async def removegame(self, ctx=SlashContext, *, game: str):
        for g in self.games:
            if (g.lower().replace(" ", "") == game.lower().replace(" ", "")):
                del self.games[g]
                break
        embed = Embed(title="Games", color=0x00ff00)
        for game in self.games:
            embed.add_field(name=game, value=self.games[game], inline=False)
        await ctx.send(embed=embed)

    @slash_command(name="changeweight", description="Change weight for game")
    @slash_option(
        name="game",
        description="The game to change weight for",
        opt_type=OptionType.STRING,
        required=True
    )
    @slash_option(
        name="weight",
        description="The new weight of the game",
        opt_type=OptionType.INTEGER,
        required=True
    )
    async def changeweight(self, ctx=SlashContext, *, game: str, weight: int):
        for g in self.games:
            if (g.lower().replace(" ", "") == game.lower().replace(" ", "")):
                self.games[g] = weight
                break
        embed = Embed(title="Games", color=0x00ff00)
        for game in self.games:
            embed.add_field(name=game, value=self.games[game], inline=False)
        await ctx.send(embed=embed)

    @listen(Component)
    async def on_component(self, event: Component):
        ctx = event.ctx
        if ctx.custom_id == "choose_again":
            await self.choose(ctx)
        elif "remove_choice_and_choose_again" in ctx.custom_id:
            choice = ctx.custom_id.split("_")[-1]
            for g in self.games:
                if (g.lower().replace(" ", "") == choice.lower().replace(" ", "")):
                    del self.games[g]
                    break
            await self.choose(ctx)


def setup(bot):
    GameChooser(bot)
