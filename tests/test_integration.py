"""End-to-end test against a minimal WebChannel server on a TCP socket."""

import asyncio
import json

from pywebchannel.qwebchannel import QWebChannelMessageTypes as MsgType
from pywebchannel.asynchronous import QWebChannelProtocol

from .test_qwebchannel import CHATSERVER


async def serve(reader, writer):
    while True:
        line = await reader.readline()
        if not line:
            break

        msg = json.loads(line)
        if msg["type"] == MsgType.init:
            data = {"chatserver": CHATSERVER}
        elif msg["type"] == MsgType.invokeMethod:
            data = f"called {msg['method']} with {msg['args']}"
        else:
            continue

        reply = {"type": MsgType.response, "id": msg["id"], "data": data}
        writer.write((json.dumps(reply) + "\n").encode())
        await writer.drain()


def test_channel_over_a_tcp_connection():
    async def main():
        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        async with server:
            loop = asyncio.get_running_loop()
            transport, channel = await loop.create_connection(
                QWebChannelProtocol, "127.0.0.1", port
            )
            try:
                await asyncio.wait_for(channel, 5)

                chatserver = channel.objects["chatserver"]
                assert chatserver.userList == ["alice"]

                result = await asyncio.wait_for(chatserver.login("bob"), 5)
                assert result == "called 3 with ['bob']"
            finally:
                transport.close()

    asyncio.run(main())
