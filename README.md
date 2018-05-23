# PyWebChannel

## What is PyWebChannel?

PyWebChannel is an impementation of [Qt's WebChannel](https://doc.qt.io/qt-5/qtwebchannel-index.html) protocol in Python.

The basic module only depends on nothing but Python's [Enum module](https://docs.python.org/3/library/enum.html).
In Python < 3.4 you need the backport `enum34` module.
Additionally, the `qwebchannel.async` submodule provides an `asyncio` compatibility layer (Python 3.5+).

A simple, newline-delimited raw TCP/IP Transport and Protocol for use with `asyncio` is provided in `qwebchannel.asyncio`.
