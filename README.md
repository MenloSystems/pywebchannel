# PyWebChannel

## What is PyWebChannel?

PyWebChannel is an implementation of [Qt's WebChannel](https://doc.qt.io/qt-5/qtwebchannel-index.html) protocol in Python.

From Python 3.4 onwards, this module has no dependencies. In Python < 3.4 you need the backport [`enum34`](https://pypi.org/project/enum34/) package.
The `pywebchannel.asynchronous` submodule provides an `asyncio` compatibility layer (Python 3.5+).

A simple, newline-delimited raw TCP/IP Transport and Protocol for use with `asyncio` is provided in `pywebchannel.asyncio`.

For an example, see the `examples/chatclient.py`. It connects to and interacts with the `chatserver` example included in QtWebChannel.
