import json

import pytest

from pywebchannel import QWebChannel
from pywebchannel import QWebChannelMessageTypes as MsgType

CHATSERVER = {
    "methods": [["login", 3], ["sendMessage", 4]],
    # [index, name, [notify signal name or 1, notify signal index], value]
    "properties": [[0, "userList", [1, 7], ["alice"]]],
    "signals": [["newMessage", 5], ["destroyed", 6]],
    "enums": {"Color": {"Red": 1, "Green": 2}},
}


class FakeTransport:
    def __init__(self):
        self.messages = []

    def send(self, data):
        self.messages.append(json.loads(data))

    def last(self, msgtype):
        return next(m for m in reversed(self.messages) if m["type"] == msgtype)


def make_channel(objects=None):
    transport = FakeTransport()
    channel = QWebChannel()
    channel.connection_made(transport)

    init = transport.messages[0]
    assert init["type"] == MsgType.init

    channel.message_received({"type": MsgType.response, "id": init["id"], "data": objects or {}})
    return channel, transport


def test_init_registers_objects():
    channel, transport = make_channel({"chatserver": CHATSERVER})

    assert list(channel.objects) == ["chatserver"]
    assert transport.messages[-1]["type"] == MsgType.idle


def test_dir_lists_the_remote_interface():
    channel, _ = make_channel({"chatserver": CHATSERVER})

    members = dir(channel.objects["chatserver"])
    assert {"login", "sendMessage", "userList", "newMessage", "Color"} <= set(members)


def test_enums_are_exposed():
    channel, _ = make_channel({"chatserver": CHATSERVER})

    assert channel.objects["chatserver"].Color.Red == 1


def test_property_starts_from_the_init_value():
    channel, _ = make_channel({"chatserver": CHATSERVER})

    assert channel.objects["chatserver"].userList == ["alice"]


def test_property_setter_sends_the_new_value():
    channel, transport = make_channel({"chatserver": CHATSERVER})
    chatserver = channel.objects["chatserver"]

    chatserver.userList = ["bob"]

    msg = transport.last(MsgType.setProperty)
    assert msg["object"] == "chatserver"
    assert msg["property"] == 0
    assert msg["value"] == ["bob"]
    assert chatserver.userList == ["bob"]


def test_property_update_runs_the_notify_callback_after_the_cache_update():
    channel, _ = make_channel({"chatserver": CHATSERVER})
    chatserver = channel.objects["chatserver"]

    seen = []
    chatserver.userListChanged.connect(lambda *args: seen.append(chatserver.userList))

    channel.message_received(
        {
            "type": MsgType.propertyUpdate,
            "data": [
                {
                    "object": "chatserver",
                    "signals": {"7": []},
                    "properties": {"0": ["alice", "bob"]},
                }
            ],
        }
    )

    assert seen == [["alice", "bob"]]


def test_signal_connect_and_emit():
    channel, transport = make_channel({"chatserver": CHATSERVER})
    chatserver = channel.objects["chatserver"]

    seen = []
    chatserver.newMessage.connect(lambda *args: seen.append(args))

    assert transport.last(MsgType.connectToSignal)["signal"] == 5

    channel.message_received(
        {"type": MsgType.signal, "object": "chatserver", "signal": 5, "args": ["12:00", "hi"]}
    )

    assert seen == [("12:00", "hi")]


def test_signal_disconnect():
    channel, transport = make_channel({"chatserver": CHATSERVER})
    chatserver = channel.objects["chatserver"]

    seen = []

    def callback(*args):
        seen.append(args)

    chatserver.newMessage.connect(callback)
    chatserver.newMessage.disconnect(callback)

    assert transport.last(MsgType.disconnectFromSignal)["signal"] == 5

    channel.message_received(
        {"type": MsgType.signal, "object": "chatserver", "signal": 5, "args": []}
    )

    assert seen == []


def test_signals_are_not_assignable():
    channel, _ = make_channel({"chatserver": CHATSERVER})

    with pytest.raises(AttributeError):
        channel.objects["chatserver"].newMessage = None


def test_method_call_passes_the_response_to_the_callback():
    channel, transport = make_channel({"chatserver": CHATSERVER})
    chatserver = channel.objects["chatserver"]

    results = []
    chatserver.login("bob", results.append)

    msg = transport.last(MsgType.invokeMethod)
    assert msg["method"] == 3
    assert msg["args"] == ["bob"]

    channel.message_received({"type": MsgType.response, "id": msg["id"], "data": True})

    assert results == [True]
    assert not channel.execCallbacks


def test_qobject_arguments_are_marshalled_by_id():
    channel, transport = make_channel({"chatserver": CHATSERVER, "other": CHATSERVER})

    channel.objects["chatserver"].sendMessage(channel.objects["other"])

    assert transport.last(MsgType.invokeMethod)["args"] == [{"id": "other"}]


def test_returned_qobjects_are_unwrapped():
    channel, transport = make_channel({"chatserver": CHATSERVER})

    results = []
    channel.objects["chatserver"].login(results.append)

    msg = transport.last(MsgType.invokeMethod)
    channel.message_received(
        {
            "type": MsgType.response,
            "id": msg["id"],
            "data": {"__QObject*__": True, "id": "room1", "data": CHATSERVER},
        }
    )

    assert results[0] is channel.objects["room1"]
    assert results[0].userList == ["alice"]


def test_destroyed_signal_unregisters_the_object():
    channel, transport = make_channel({"chatserver": CHATSERVER})

    results = []
    channel.objects["chatserver"].login(results.append)
    msg = transport.last(MsgType.invokeMethod)
    channel.message_received(
        {
            "type": MsgType.response,
            "id": msg["id"],
            "data": {"__QObject*__": True, "id": "room1", "data": CHATSERVER},
        }
    )

    channel.message_received({"type": MsgType.signal, "object": "room1", "signal": 6, "args": []})

    assert "room1" not in channel.objects


def test_init_unwraps_properties_with_non_contiguous_indices():
    server = {
        "methods": [],
        "properties": [[9, "peer", None, {"__QObject*__": True, "id": "chatserver"}]],
        "signals": [],
    }
    channel, _ = make_channel({"chatserver": CHATSERVER, "client": server})

    assert channel.objects["client"].peer is channel.objects["chatserver"]


def test_messages_before_initialization_are_ignored():
    transport = FakeTransport()
    channel = QWebChannel()
    channel.connection_made(transport)

    channel.message_received(
        {"type": MsgType.signal, "object": "chatserver", "signal": 5, "args": []}
    )

    assert MsgType.idle not in [m["type"] for m in transport.messages]


def test_init_callback_is_invoked():
    seen = []
    transport = FakeTransport()
    channel = QWebChannel(seen.append)
    channel.connection_made(transport)
    channel.message_received(
        {"type": MsgType.response, "id": transport.messages[0]["id"], "data": {}}
    )

    assert seen == [channel]


def test_unknown_response_id_is_ignored():
    channel, _ = make_channel({"chatserver": CHATSERVER})

    channel.message_received({"type": MsgType.response, "id": 4711, "data": None})


def test_string_messages_are_decoded():
    channel, _ = make_channel({"chatserver": CHATSERVER})

    seen = []
    channel.objects["chatserver"].newMessage.connect(lambda *args: seen.append(args))
    channel.message_received(
        json.dumps({"type": MsgType.signal, "object": "chatserver", "signal": 5, "args": ["hi"]})
    )

    assert seen == [("hi",)]


def test_debug_message():
    channel, transport = make_channel()

    channel.debug("hello")

    assert transport.last(MsgType.debug)["data"] == "hello"


def test_second_connect_does_not_send_another_connect_request():
    """
    @scenario Connecting a second callback to a signal
    @given A signal with one connected callback
    @when  A second callback connects to the same signal
    @then  The server receives only one connectToSignal request, so that one
           disconnectFromSignal later releases the server-side connection
    """
    # GIVEN: The newMessage signal has one callback.
    channel, transport = make_channel({"chatserver": CHATSERVER})
    chatserver = channel.objects["chatserver"]
    chatserver.newMessage.connect(print)

    # WHEN: A second callback connects.
    chatserver.newMessage.connect(repr)

    # THEN: Only one connectToSignal request was sent.
    sent = [m["type"] for m in transport.messages]
    assert sent.count(MsgType.connectToSignal) == 1


def test_repeated_destroyed_signal_is_ignored():
    """
    @scenario The server reports the destruction of an object twice
    @given An unwrapped QObject that the destroyed signal already removed
    @when  The destroyed signal arrives again for the same object
    @then  The channel does not raise
    """
    # GIVEN: room1 is unwrapped and then destroyed.
    channel, transport = make_channel({"chatserver": CHATSERVER})
    channel.objects["chatserver"].login(print)
    msg = transport.last(MsgType.invokeMethod)
    channel.message_received(
        {
            "type": MsgType.response,
            "id": msg["id"],
            "data": {"__QObject*__": True, "id": "room1", "data": CHATSERVER},
        }
    )
    room1 = channel.objects["room1"]
    destroyed = {"type": MsgType.signal, "object": "room1", "signal": 6, "args": []}
    channel.message_received(destroyed)

    # WHEN: The destroyed signal is emitted again on the stale object.
    room1._signalEmitted(6, [])

    # THEN: The object stays unregistered.
    assert "room1" not in channel.objects


def test_disconnecting_from_destroyed_sends_no_disconnect_request():
    """
    @scenario Disconnecting the last callback of the destroyed signal
    @given A callback connected to the destroyed signal, which sent no connect request
    @when  The callback disconnects
    @then  The server receives no disconnectFromSignal request either
    """
    # GIVEN: A callback is connected to the destroyed signal.
    channel, transport = make_channel({"chatserver": CHATSERVER})
    chatserver = channel.objects["chatserver"]
    chatserver.destroyed.connect(print)

    # WHEN: The callback disconnects.
    chatserver.destroyed.disconnect(print)

    # THEN: No disconnectFromSignal request was sent.
    sent = [m["type"] for m in transport.messages]
    assert MsgType.disconnectFromSignal not in sent


def test_no_idle_is_sent_when_the_init_callback_closes_the_connection():
    """
    @scenario The init callback closes the connection
    @given A channel whose init callback closes the connection
    @when  The init response arrives
    @then  The channel sends no idle message over the closed connection
    """
    # GIVEN: The init callback closes the connection.
    transport = FakeTransport()
    channel = QWebChannel(lambda channel: channel.connection_closed())
    channel.connection_made(transport)

    # WHEN: The init response arrives.
    channel.message_received(
        {"type": MsgType.response, "id": transport.messages[0]["id"], "data": {}}
    )

    # THEN: No idle message was sent.
    assert MsgType.idle not in [m["type"] for m in transport.messages]


def test_closing_the_connection_drops_pending_callbacks():
    """
    @scenario The connection closes while a method call is pending
    @given A channel with a pending method call
    @when  The connection closes
    @then  The channel no longer holds the callback of the call
    """
    # GIVEN: A method call waits for its response.
    channel, _ = make_channel({"chatserver": CHATSERVER})
    channel.objects["chatserver"].login("bob", print)

    # WHEN: The connection closes.
    channel.connection_closed()

    # THEN: The callback is gone.
    assert not channel.execCallbacks
