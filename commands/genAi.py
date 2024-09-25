from interactions import Extension, OptionType, slash_command, slash_option
import os
from dotenv import load_dotenv
import google.generativeai as palm
from interactions import SlashContext


class GenAI(Extension):
    def __init__(self, bot):
        load_dotenv()
        palm.configure(api_key=os.getenv("PALM_API_KEY"))

    @slash_command(name="writecode", description="Write code for you")
    @slash_option(
        name="message",
        description="The message to write code for",
        opt_type=OptionType.STRING,
        required=True
    )
    async def writecode(self, ctx=SlashContext, *, message: str):
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
    async def solutionsguy(self, ctx=SlashContext, *, message: str):
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
    async def chat(self, ctx=SlashContext):
        test = """Attention all computer science students! Are you tired of losing grades and facing academic penalties for copying code? If yes, Atharav Solutions is here to help you.

    Atharav, a student just like you, faced the same problem and failed a class three times due to his inability to copy code effectively. He was determined to find a solution and created Atharav Solutions to help other students like him.

    Our company specializes in changing code so it does not look like copied code, helping you avoid plagiarism penalties. With Atharav Solutions, you can say goodbye to lost grades and hello to successful academic careers.

    Our team of experts uses innovative techniques to ensure that your code is unique and original, and that you receive the grades you deserve. With Atharav Solutions, you can focus on learning and growing in your computer science studies without the fear of academic penalties.

    So, if you're ready to put an end to the problem of copying code and succeed in your computer science classes, choose Atharav Solutions. Contact us today and let us help you achieve your academic goals!

    https://atharav-solutions.onrender.com/
        """
        await ctx.send(test)


def setup(bot):
    GenAI(bot)
