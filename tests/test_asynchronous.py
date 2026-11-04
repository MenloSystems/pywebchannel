import asyncio
import gc
import json

import pytest

from pywebchannel import QWebChannelMessageTypes as MsgType
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


def test_a_cancelled_call_does_not_raise_in_the_loop():
    errors = []

    async def main():
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, context: errors.append(context)
        )

        transport = FakeTransport()
        channel = QWebChannel()
        init(channel, transport)
        await channel

        result = channel.objects["chatserver"].login("bob")
        result.cancel()

        msg = transport.last(MsgType.invokeMethod)
        channel.message_received({"type": MsgType.response, "id": msg["id"], "data": True})

        # let the queued call_soon_threadsafe() callback run
        await asyncio.sleep(0)

    asyncio.run(main())
    assert errors == []


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

        channel.connection_lost(None)

    asyncio.run(main())


def test_pending_call_fails_when_the_connection_closes():
    """
    @scenario The connection closes while a method call is pending
    @given An initialized channel with a pending method call
    @when  The connection closes
    @then  Awaiting the call raises ConnectionError instead of hanging
    """

    async def main():
        # GIVEN: A method call waits for its response.
        transport = FakeTransport()
        channel = QWebChannel()
        init(channel, transport)
        await channel
        result = channel.objects["chatserver"].login("bob")

        # WHEN: The connection closes.
        channel.connection_closed()

        # THEN: The call fails.
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(result, 1)

    asyncio.run(main())


def test_awaiting_the_channel_fails_when_the_connection_closes_before_init():
    """
    @scenario The connection closes before the initialization completes
    @given A channel that waits for the init response
    @when  The connection closes
    @then  Awaiting the channel raises ConnectionError instead of hanging
    """

    async def main():
        # GIVEN: The init request is sent, but not answered.
        channel = QWebChannel()
        channel.connection_made(FakeTransport())

        # WHEN: The connection closes.
        channel.connection_closed()

        # THEN: Awaiting the channel fails.
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(channel, 1)

    asyncio.run(main())


def test_double_initialization_does_not_raise_in_the_loop():
    """
    @scenario The channel initializes twice before the loop runs
    @given A channel whose initialization was signalled once
    @when  The initialization is signalled again before the loop runs
    @then  No error reaches the exception handler of the loop
    """
    errors = []

    async def main():
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, context: errors.append(context)
        )

        # GIVEN: The channel signalled its initialization once.
        channel = QWebChannel()
        channel.initialized()

        # WHEN: It signals the initialization again.
        channel.initialized()

        # THEN: Both queued callbacks run without errors.
        await channel

    asyncio.run(main())
    assert errors == []


def test_channel_can_be_created_and_initialized_outside_a_running_loop():
    """
    @scenario The channel is set up before the event loop runs
    @given A channel created and initialized without a running loop
    @when  The channel is awaited inside a loop
    @then  The await completes and the objects are available
    """
    # GIVEN: The channel is created and initialized before any loop runs.
    transport = FakeTransport()
    channel = QWebChannel()
    init(channel, transport)

    # WHEN: A loop awaits the channel.
    async def main():
        await asyncio.wait_for(channel, 1)

    asyncio.run(main())

    # THEN: The channel is initialized.
    assert "chatserver" in channel.objects


def test_awaiting_fails_when_the_connection_closed_before_any_loop_ran():
    """
    @scenario The connection closes before the event loop runs
    @given A channel whose connection closed without a running loop
    @when  The channel is awaited inside a loop
    @then  Awaiting the channel raises ConnectionError
    """
    # GIVEN: The connection closes before any loop runs.
    channel = QWebChannel()
    channel.connection_made(FakeTransport())
    channel.connection_closed()

    # WHEN: A loop awaits the channel.
    async def main():
        await asyncio.wait_for(channel, 1)

    # THEN: The await fails.
    with pytest.raises(ConnectionError):
        asyncio.run(main())


def test_unawaited_channel_does_not_log_an_error_when_the_connection_closes():
    """
    @scenario Nobody waits for the initialization
    @given A channel inside a loop that is never awaited
    @when  The connection closes before the initialization
    @then  No error reaches the exception handler of the loop
    """
    errors = []

    async def main():
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, context: errors.append(context)
        )

        # GIVEN: A channel that nobody awaits.
        channel = QWebChannel()
        channel.connection_made(FakeTransport())

        # WHEN: The connection closes.
        channel.connection_closed()
        del channel
        await asyncio.sleep(0)
        gc.collect()

    asyncio.run(main())

    # THEN: The loop saw no unretrieved exception.
    assert errors == []


def test_call_after_the_connection_closed_fails():
    """
    @scenario A method is called after the connection closed
    @given An initialized channel whose connection closed
    @when  A remote method is called
    @then  Awaiting the call raises ConnectionError instead of hanging
    """

    async def main():
        # GIVEN: The connection of an initialized channel closed.
        transport = FakeTransport()
        channel = QWebChannel()
        init(channel, transport)
        await channel
        channel.connection_closed()

        # WHEN: A remote method is called.
        result = channel.objects["chatserver"].login("bob")

        # THEN: The call fails.
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(result, 1)

    asyncio.run(main())


def test_channel_can_be_awaited_after_a_reconnect():
    """
    @scenario The channel reconnects after the connection closed before init
    @given A channel whose first connection closed before the initialization
    @when  A new connection is made and initialized
    @then  Awaiting the channel completes
    """

    async def main():
        # GIVEN: The first connection closed before the initialization.
        channel = QWebChannel()
        channel.connection_made(FakeTransport())
        channel.connection_closed()

        # WHEN: A new connection initializes the channel.
        init(channel, FakeTransport())

        # THEN: Awaiting the channel completes.
        await asyncio.wait_for(channel, 1)

    asyncio.run(main())


def test_unawaited_call_does_not_log_an_error_when_the_connection_closes():
    """
    @scenario Nobody waits for the result of a method call
    @given A pending method call whose future is never awaited
    @when  The connection closes
    @then  No error reaches the exception handler of the loop
    """
    errors = []

    async def main():
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, context: errors.append(context)
        )

        # GIVEN: A method call that nobody awaits.
        transport = FakeTransport()
        channel = QWebChannel()
        init(channel, transport)
        await channel
        channel.objects["chatserver"].sendMessage("hi")

        # WHEN: The connection closes.
        channel.connection_closed()
        await asyncio.sleep(0)
        del channel
        gc.collect()

    asyncio.run(main())

    # THEN: The loop saw no unretrieved exception.
    assert errors == []


class InitCallbackError(Exception):
    pass


def failing_init_callback(channel):
    raise InitCallbackError


def test_awaiting_fails_when_the_init_callback_raises():
    """
    @scenario The init callback raises
    @given A channel whose init callback raises
    @when  The init response arrives
    @then  Awaiting the channel raises the error instead of hanging
    """

    async def main():
        # GIVEN: The init callback raises.
        channel = QWebChannel(failing_init_callback)
        awaiting = asyncio.ensure_future(asyncio.wait_for(channel, 1))
        await asyncio.sleep(0)

        # WHEN: The init response arrives.
        with pytest.raises(InitCallbackError):
            init(channel, FakeTransport())

        # THEN: Awaiting the channel raises the error of the callback.
        with pytest.raises(InitCallbackError):
            await awaiting

    asyncio.run(main())


def test_pending_calls_get_distinct_errors_when_the_connection_closes():
    """
    @scenario Several method calls are pending when the connection closes
    @given An initialized channel with two pending method calls
    @when  The connection closes
    @then  Each call fails with its own ConnectionError instance
    """

    async def main():
        # GIVEN: Two method calls wait for their responses.
        channel = QWebChannel()
        init(channel, FakeTransport())
        await channel
        first = channel.objects["chatserver"].login("alice")
        second = channel.objects["chatserver"].login("bob")

        # WHEN: The connection closes.
        channel.connection_closed()
        await asyncio.sleep(0)

        # THEN: The errors are not shared.
        assert first.exception() is not second.exception()

    asyncio.run(main())


def test_closing_after_the_loop_closed_does_not_raise():
    """
    @scenario The connection closes after the loop of a pending call closed
    @given A pending method call whose loop has closed
    @when  The connection closes
    @then  connection_closed() does not raise
    """

    # GIVEN: A method call is pending when its loop ends.
    channel = QWebChannel()

    async def main():
        init(channel, FakeTransport())
        await channel
        return channel.objects["chatserver"].login("bob")

    pending = asyncio.run(main())

    # WHEN: The connection closes.
    channel.connection_closed()

    # THEN: The future stays pending, as its loop cannot run any more.
    assert not pending.done()
