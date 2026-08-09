"""Which VoiceClient class the bot connects with.

A guild only ever has one voice client, and recording needs the subclass from
discord-ext-voice-recv, so every connection uses it whether or not anyone is
recording — otherwise /record would depend on nobody having started music first.

The import is soft: without the extension installed the bot still runs normally,
just without /record.
"""
import discord

try:
    from discord.ext import voice_recv

    VoiceClientCls = voice_recv.VoiceRecvClient
    RECV_AVAILABLE = True
    RECV_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - depends on the deployment
    voice_recv = None
    VoiceClientCls = discord.VoiceClient
    RECV_AVAILABLE = False
    RECV_IMPORT_ERROR = exc


MISSING_DEPENDENCY_MESSAGE = (
    "Voice recording isn't available: the `discord-ext-voice-recv` package "
    "isn't installed. Add it with `pip install discord-ext-voice-recv` and "
    "restart the bot."
)
