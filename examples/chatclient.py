#!/usr/bin/env python3
"""Chat client for the `chatserver` example that comes with QtWebChannel.

Run it with the optional dependencies installed:

    pip install -e ".[examples]"
    python examples/chatclient.py
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import Any

from aioconsole import ainput
from websockets.asyncio.client import ClientConnection, connect

from pywebchannel.asynchronous import QWebChannel

CHATSERVER_URL = "ws://localhost:12345"


class WebSocketTransport:
    """Adapts a websockets connection to the transport interface of QWebChannel.

    QWebChannel.send() is synchronous, so every message is sent in its own task.
    """

    def __init__(self, connection: ClientConnection) -> None:
        self._connection = connection
        self._tasks: set[asyncio.Task[None]] = set()

    def send(self, data: str) -> None:
        task = asyncio.create_task(self._connection.send(data))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


async def read_messages(connection: ClientConnection, webchannel: QWebChannel) -> None:
    try:
        async for message in connection:
            webchannel.message_received(message)
    finally:
        # Fail the pending calls, so that the chat does not wait forever
        webchannel.connection_closed()


def print_newmessage(time: str, user: str, message: str) -> None:
    print(f"[{time}] {user}: {message}")


def make_userlist_printer() -> Callable[[Any], None]:
    userlist: list[str] = []

    def print_newusers(chatserver: Any) -> None:
        nonlocal userlist

        if chatserver.userList != userlist:
            userlist = chatserver.userList
            print(f"User list changed: {userlist}")

    return print_newusers


async def chat(webchannel: QWebChannel) -> None:
    # Wait for the initialization to complete
    await webchannel
    print("Connected.")

    chatserver = webchannel.objects["chatserver"]

    async def login() -> str | None:
        name = await ainput("Enter your name: ")
        return name if await chatserver.login(name) else None

    # Loop until we get a valid username
    username = await login()
    while username is None:
        print("Username already taken. Please enter a new one.")
        username = await login()

    # Keep the username alive
    chatserver.keepAlive.connect(lambda *args: chatserver.keepAliveResponse(username))

    # Connect to the chat signals
    chatserver.newMessage.connect(print_newmessage)
    print_newusers = make_userlist_printer()
    chatserver.userListChanged.connect(lambda *args: print_newusers(chatserver))

    # Read and send input
    while True:
        message = await ainput()
        chatserver.sendMessage(username, message)


async def main() -> None:
    print("Connecting...")
    async with connect(CHATSERVER_URL) as connection:
        webchannel = QWebChannel()
        webchannel.connection_made(WebSocketTransport(connection))

        reader = asyncio.create_task(read_messages(connection, webchannel))
        try:
            await chat(webchannel)
        finally:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt, EOFError):
        asyncio.run(main())
    print("Quit.")
