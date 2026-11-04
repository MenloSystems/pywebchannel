# PyWebChannel

## What is PyWebChannel?

PyWebChannel is an implementation of [Qt's WebChannel](https://doc.qt.io/qt-6/qtwebchannel-index.html)
protocol in pure Python. It lets Python code use the objects, properties, signals and methods that a
Qt application publishes through `QWebChannel`.

The library has no dependencies. It needs Python 3.10 or later.

## Installation

```
pip install pywebchannel
```

## Modules

* `pywebchannel` — the transport-agnostic protocol implementation. Give the channel a transport that
  has a `send()` method, and pass each received message to `QWebChannel.message_received()`.
* `pywebchannel.asynchronous` — an `asyncio` layer. Remote methods return awaitables, and you can
  await the channel itself to wait for the initialization. `QWebChannelProtocol` is an
  `asyncio.Protocol` for streaming transports with newline-delimited messages.

## Example

```python
import asyncio

from pywebchannel.asynchronous import QWebChannelProtocol


async def main():
    loop = asyncio.get_running_loop()
    _, channel = await loop.create_connection(QWebChannelProtocol, "localhost", 12345)

    # Wait until the remote objects are available
    await channel

    obj = channel.objects["myObject"]

    obj.mySignal.connect(print)  # signals take callbacks
    obj.myProperty = 42  # properties behave like attributes
    print(await obj.myMethod("a"))  # methods return awaitables


asyncio.run(main())
```

Use `dir(obj)` to see the interface of a remote object.

A channel serves a single connection. Reconnecting is not supported, so create a new channel
for a new connection.

`examples/chatclient.py` is a larger example. It connects to the `chatserver` example that comes
with QtWebChannel:

```
pip install -e ".[examples]"
python examples/chatclient.py
```

## Development

```
pip install -e . pytest ruff
pytest
ruff check .
ruff format .
```

## License

PyWebChannel is dual-licensed: LGPL-3.0-only OR GPL-2.0-or-later.
