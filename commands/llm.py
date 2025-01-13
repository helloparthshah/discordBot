import asyncio
import discord
import os
from dotenv import load_dotenv
import requests
from discord.ext import commands
from discord import app_commands
from ollama import chat
from ollama import ChatResponse
import ollama
import re


class LLM(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.models = {
            "manager": "assets/models/manager.modelfile",
            "chussu": "assets/models/chussu.modelfile",
            "keya": "assets/models/keya.modelfile"
        }
        for model, filename in self.models.items():
            if not self.isModelLoaded(model):
                print(f"Creating model {model}")
                with open(filename, 'r', encoding="utf8") as f:
                    modelfile = f.read()
                    ollama.create(model=model, modelfile=modelfile)

        self.available_functions = {
            'consultAgent': self.consultAgent,
            'chussu': self.consultChussu,
            'keya': self.consultKeya,
            'wikipedia': self.searchWikipedia,
        }

    def isModelLoaded(self, model):
        self.loaded_models = [model.model for model in ollama.list().models]
        return model in self.loaded_models or f'{model}:latest' in self.loaded_models

    def consultAgent(self, agent, question):
        print("Consulting agent", agent, "with question", question)
        if not self.isModelLoaded(agent):
            print(f"Model {agent} not found")
            return
        response: ChatResponse = chat(model=agent, messages=[
            {
                'role': 'user',
                'content': question,
            },
        ])
        return response.message.content

    def consultChussu(self, question):
        return self.consultAgent('chussu', question)

    def consultKeya(self, question):
        return self.consultAgent('keya', question)

    def searchWikipedia(self, question):
        print(f"Searching Wikipedia for: {question}")
        url = f"https://en.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": question,
            "srlimit": 1,
        }
        response = requests.get(url, params=params)
        if response.status_code == 200:
            data = response.json()
            if "query" in data and "search" in data["query"] and len(data["query"]["search"]) > 0:
                top_result = data["query"]["search"][0]
                title = top_result["title"]
                snippet = re.sub(r'<[^>]*>', '', top_result["snippet"])
                page_url = f"https://en.wikipedia.org/wiki/{
                    title.replace(' ', '_')}"
                return f"**{title}**\n{snippet}\n{page_url}"
            else:
                return "No results found on Wikipedia."
        else:
            return "Failed to fetch data from Wikipedia."

    async def askManager(self, messages, inter):
        response: ChatResponse = chat(model='manager', messages=messages, tools=[
            {
                'type': 'function',
                'function': {
                    'name': 'wikipedia',
                    'description': 'Consult Wikipedia for questions related to general knowledge and facts.',
                    'parameters': {
                        'type': 'object',
                        'required': ['question'],
                        'properties': {
                            'question': {'type': 'string', 'description': 'The question to search Wikipedia for'},
                        },
                    },
                },
            },
            {
                'type': 'function',
                'function': {
                    'name': 'chussu',
                    'description': 'Consult chussu for questions related to relationships.',
                    'parameters': {
                        'type': 'object',
                        'required': ['question'],
                        'properties': {
                            'question': {'type': 'string', 'description': 'The question to ask Chussu'},
                        },
                    },
                },
            },
            {
                'type': 'function',
                'function': {
                    'name': 'keya',
                    'description': 'Consult keya for questions related to working at Netflix and programming.',
                    'parameters': {
                        'type': 'object',
                        'required': ['question'],
                        'properties': {
                            'question': {'type': 'string', 'description': 'The question to ask Keya'},
                        },
                    },
                },
            }
        ])
        print(response.message.tool_calls)
        messages.append({
            'role': 'assistant',
            'content': response.message.content,
        })
        if "DONE" in response.message.content:
            messages[-1]['content'] = messages[-1]['content'].replace("DONE", "")
            return messages
        if response.message.tool_calls:
            for tool in response.message.tool_calls:
                if function_to_call := self.available_functions.get(tool.function.name):
                    await self.sendInChunks(inter, "Asking "+tool.function.name+": " + tool.function.arguments['question'])
                    output = function_to_call(**tool.function.arguments)
                    messages.append({'role': 'tool', 'content': str(
                        output), 'name': tool.function.name})
                    await self.sendInChunks(inter, tool.function.name+": " + output)
                else:
                    raise ValueError(
                        f"Function {tool.function.name} not available")

            messages = await self.askManager(messages, inter)
        else:
            print('No tool calls returned from model')
        return messages

    @app_commands.command(name="llm", description="Talk to llm")
    @app_commands.describe(
        question="The question to ask llm",
    )
    async def llm(self, inter: discord.Interaction, question: str):
        await inter.response.defer()
        messages = [{
            'role': 'user',
            'content': question,
        }]

        responses = await self.askManager(messages, inter)
        for response in responses[-1:]:
            if (response['content']):
                await self.sendInChunks(inter, "Manager: "+response['content'])

    async def sendInChunks(self, inter, message):
        for i in range(0, len(message), 2000):
            await inter.followup.send(message[i:i+2000])


async def setup(bot):
    print("Adding llm")
    await bot.add_cog(LLM(bot))


async def teardown(bot):
    print("Unloaded llm")
