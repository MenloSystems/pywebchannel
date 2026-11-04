import asyncio
import gc
import json

import pytest

from pywebchannel import QWebChannelMessageTypes as MsgType
from pywebchannel.asynchronous import QWebChannel, QWebChannelProtocol

from .test_qwebchannel import CHATSERVER, NEW_MESSAGE_INDEX, FailingTransport, FakeTransport

# Short enough to time out before a response that is sent later
SHORT_TIMEOUT = 0.01


class FakeStreamTransport:
    def __init__(self):
        self.data = b""

    def write(self, data):
        self.data += data

    def messages(self):
        return [json.loads(line) for line in self.data.splitlines()]


def encode_response(requestId, data=None):
    message = {
        "type": MsgType.response,
        "id": requestId,
        "data": data or {"chatserver": CHATSERVER},
    }
    return json.dumps(message).encode()


def encode_signal(signalIndex, *args):
    message = {"type": MsgType.signal, "object": "chatserver", "signal": signalIndex, "args": args}
    return json.dumps(message).encode()


@pytest.fixture
def loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


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
    """
    @scenario Messages arrive in arbitrary chunks
    @given A protocol channel whose init callback connects to a signal
    @when  The init response arrives in two chunks, the second also holding a signal
    @then  The channel initializes and the signal reaches the callback
    """

    async def main():
        # GIVEN: The init callback connects to newMessage.
        seen = []
        transport = FakeStreamTransport()
        channel = QWebChannelProtocol(
            lambda ch: ch.objects["chatserver"].newMessage.connect(lambda *args: seen.append(args))
        )
        channel.connection_made(transport)
        response = encode_response(transport.messages()[0]["id"])
        signal = encode_signal(NEW_MESSAGE_INDEX, "hi")

        # WHEN: The response is split, and the signal follows in the same chunk.
        channel.data_received(response[:10])
        channel.data_received(response[10:] + b"\n" + signal + b"\n")

        # THEN: The channel is initialized and the signal was delivered.
        await asyncio.wait_for(channel, 1)
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


def test_cancelled_await_does_not_affect_later_awaits():
    """
    @scenario An await of the channel times out before the initialization
    @given A channel whose first await was cancelled by a timeout
    @when  The init response arrives
    @then  A later await of the channel completes
    """

    async def main():
        # GIVEN: The first await timed out.
        transport = FakeTransport()
        channel = QWebChannel()
        channel.connection_made(transport)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(channel, SHORT_TIMEOUT)

        # WHEN: The init response arrives.
        channel.message_received(encode_response(transport.messages[0]["id"]))

        # THEN: Awaiting the channel again completes.
        await asyncio.wait_for(channel, 1)

    asyncio.run(main())


def test_failed_send_keeps_no_pending_future():
    """
    @scenario Sending a call fails
    @given An initialized channel whose transport cannot send
    @when  A remote method is called
    @then  The error propagates and no future is kept as pending
    """

    async def main():
        # GIVEN: The transport fails from now on.
        channel = QWebChannel()
        init(channel, FakeTransport())
        await channel
        channel.transport = FailingTransport()

        # WHEN: A method is called.
        with pytest.raises(OSError):
            channel.objects["chatserver"].login("bob")

        # THEN: No future waits for a response.
        assert channel._pendingFutures == set()

    asyncio.run(main())


def test_explicit_loop_allows_calls_outside_a_running_loop(loop):
    """
    @scenario The channel is used before its loop runs
    @given A channel created and initialized with an explicit loop that does not run
    @when  A remote method is called and answered outside the loop
    @then  The loop can await the result
    """
    # GIVEN: The channel uses an explicit loop.
    transport = FakeTransport()
    channel = QWebChannel(loop=loop)
    init(channel, transport)

    # WHEN: A method is called and answered without a running loop.
    result = channel.objects["chatserver"].login("bob")
    msg = transport.last(MsgType.invokeMethod)
    channel.message_received({"type": MsgType.response, "id": msg["id"], "data": True})

    # THEN: The loop gets the result.
    assert loop.run_until_complete(asyncio.wait_for(result, 1)) is True


def test_protocol_skips_empty_messages():
    """
    @scenario The stream holds empty lines
    @given A protocol channel waiting for the init response
    @when  Empty lines arrive before and after the response
    @then  The channel initializes
    """

    async def main():
        # GIVEN: The channel waits for the init response.
        transport = FakeStreamTransport()
        channel = QWebChannelProtocol()
        channel.connection_made(transport)
        response = encode_response(transport.messages()[0]["id"])

        # WHEN: The response is surrounded by empty lines.
        channel.data_received(b"\n\n" + response + b"\n\n")

        # THEN: The channel is initialized.
        await asyncio.wait_for(channel, 1)

    asyncio.run(main())
