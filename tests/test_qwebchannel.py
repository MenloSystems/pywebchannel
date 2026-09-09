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
