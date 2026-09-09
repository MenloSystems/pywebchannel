import asyncio
import json

from pywebchannel.qwebchannel import QWebChannelMessageTypes as MsgType
from pywebchannel.asynchronous import QWebChannel, QWebChannelProtocol

from .test_qwebchannel import CHATSERVER, FakeTransport


class FakeStreamTransport:
    def __init__(self):
        self.data = b""

    def write(self, data):
        self.data += data

    def messages(self):
        return [json.loads(line) for line in self.data.splitlines()]


def init(channel, transport):
    channel.connection_made(transport)
    channel.message_received(
        {
            "type": MsgType.response,
            "id": transport.messages[0]["id"],
            "data": {"chatserver": CHATSERVER},
        }
    )


def test_channel_can_be_awaited_for_initialization():
    async def main():
        transport = FakeTransport()
        channel = QWebChannel()
        init(channel, transport)

        await asyncio.wait_for(channel, 1)
        return channel

    channel = asyncio.run(main())
    assert "chatserver" in channel.objects


def test_awaiting_the_channel_twice_works():
    async def main():
        transport = FakeTransport()
        channel = QWebChannel()
        init(channel, transport)

        await channel
        await channel

    asyncio.run(main())


def test_methods_return_awaitables():
    async def main():
        transport = FakeTransport()
        channel = QWebChannel()
        init(channel, transport)
        await channel

        result = channel.objects["chatserver"].login("bob")

        msg = transport.last(MsgType.invokeMethod)
        assert msg["args"] == ["bob"]
        channel.message_received({"type": MsgType.response, "id": msg["id"], "data": True})

        return await asyncio.wait_for(result, 1)

    assert asyncio.run(main()) is True


def test_methods_keep_the_isqtmethod_marker():
    async def main():
        channel = QWebChannel()
        init(channel, FakeTransport())
        return channel.objects["chatserver"]

    chatserver = asyncio.run(main())
    assert chatserver.login.isQtMethod
    assert "login" in dir(chatserver)


def test_protocol_splits_newline_delimited_messages():
    async def main():
        transport = FakeStreamTransport()
        channel = QWebChannelProtocol()
        channel.connection_made(transport)

        init_id = transport.messages()[0]["id"]
        response = json.dumps(
            {"type": MsgType.response, "id": init_id, "data": {"chatserver": CHATSERVER}}
        ).encode()

        # Feed the response in two chunks, plus a signal in the same chunk as the newline
        channel.data_received(response[:10])
        channel.data_received(response[10:] + b"\n")

        await channel

        seen = []
        channel.objects["chatserver"].newMessage.connect(lambda *args: seen.append(args))
        channel.data_received(
            json.dumps(
                {"type": MsgType.signal, "object": "chatserver", "signal": 5, "args": ["hi"]}
            ).encode()
            + b"\n"
        )

        assert seen == [("hi",)]
        assert MsgType.idle in [m["type"] for m in transport.messages()]

    asyncio.run(main())
