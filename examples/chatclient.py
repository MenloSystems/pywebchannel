from pywebchannel.async import QWebChannel
import websockets
import websockets.client
import asyncio
from aioconsole import ainput
import json
import sys


CHATSERVER_URL = 'ws://localhost:12345'


class QWebChannelWebSocketProtocol(websockets.client.WebSocketClientProtocol):
    """ Bridges WebSocketClientProtocol and QWebChannel.

    Continuously reads messages in a task and invokes QWebChannel.message_received()
    for each. Calls QWebChannel.connection_open() when connected.
    Also patches QWebChannel.send() to run the websocket's send() in a task"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _task_send(self, data):
        if not isinstance(data, str):
            data = json.dumps(data)
        self.loop.create_task(self.send(data))

    def connection_open(self):
        super().connection_open()

        self.webchannel = QWebChannel()
        self.webchannel.send = self._task_send
        self.webchannel.connection_made(self)

        self.loop.create_task(self.read_msgs())

    async def read_msgs(self):
        async for msg in self:
            self.webchannel.message_received(msg)


def print_newmessage(time, user, message):
    print("[{}] {}: {}".format(time, user, message))


userlist = []

def print_newusers(chatserver):
    global userlist

    if chatserver.userList != userlist:
        userlist = chatserver.userList
        print("User list changed: {}".format(userlist))


async def run(webchannel):
    # Wait for initialized
    await webchannel
    print("Connected.")

    chatserver = webchannel.objects["chatserver"]

    username = None

    async def login():
        nonlocal username
        username = input("Enter your name: ")
        return await chatserver.login(username)

    # Loop until we get a valid username
    while not await login():
        print("Username already taken. Please enter a new one.")

    # Keep the username alive
    chatserver.keepAlive.connect(lambda *args: chatserver.keepAliveResponse(username))

    # Connect to chat signals
    chatserver.newMessage.connect(print_newmessage)
    chatserver.userListChanged.connect(lambda *args: print_newusers(chatserver))

    # Read and send input
    while True:
        msg = await ainput()
        chatserver.sendMessage(username, msg)

try:
    loop = asyncio.get_event_loop()
    print("Connecting...")
    proto = loop.run_until_complete(websockets.client.connect(CHATSERVER_URL, create_protocol=QWebChannelWebSocketProtocol))
    loop.run_until_complete(run(proto.webchannel))
except KeyboardInterrupt:
    print("Quit.")
