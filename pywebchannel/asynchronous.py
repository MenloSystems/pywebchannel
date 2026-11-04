# Copyright (C) 2016 - 2018, Menlo Systems GmbH
#
# SPDX-License-Identifier: LGPL-3.0-only OR GPL-2.0-or-later

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable, Generator
from typing import Any

from .qwebchannel import Message
from .qwebchannel import QObject as PlainQObject
from .qwebchannel import QWebChannel as PlainQWebChannel

__all__ = ["QObject", "QWebChannel", "QWebChannelProtocol"]


class QObject(PlainQObject):
    """A QObject whose remote methods return awaitables instead of taking a callback."""

    def _addMethod(self, methodData: list[Any]) -> None:
        super()._addMethod(methodData)

        methodName = methodData[0]
        method = getattr(self, methodName)

        def amethod(*args: Any) -> asyncio.Future[Any]:
            loop = self._webChannel._getLoop()
            fut = loop.create_future()

            def handleResponse(response: Any) -> None:
                QWebChannel._callSoon(fut, QWebChannel._resolve, fut, response)

            if self._webChannel._trackPending(fut):
                try:
                    method(*args, handleResponse)
                except BaseException:
                    self._webChannel._pendingFutures.discard(fut)
                    raise
            return fut

        amethod.isQtMethod = True  # type: ignore[attr-defined]

        setattr(self, methodName, amethod)


class QWebChannel(PlainQWebChannel):
    """A QWebChannel that can be awaited to wait for the initialization to complete.

    The channel can be created without a running event loop. It uses the explicit `loop`
    if one is given, and otherwise the loop that runs when the channel is awaited or a
    remote method is called. So without an explicit `loop`, call remote methods from
    within a running loop.

    If the connection closes before the initialization completes, awaiting the channel
    raises ConnectionError. Remote methods called after the close fail with
    ConnectionError. Reconnecting is not supported, so use a new channel instead.

    Like the base channel, this channel is not thread-safe. Use it from one thread only,
    usually the thread of its event loop.
    """

    QObjectType = QObject

    def __init__(
        self,
        initCallback: Callable[[PlainQWebChannel], None] | None = None,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        super().__init__(initCallback)

        self._explicitLoop = loop

        self._pendingFutures: set[asyncio.Future[Any]] = set()

        # The init future only exists once someone awaits the channel. The outcome is
        # kept until then.
        self._initFuture: asyncio.Future[None] | None = None
        self._initDone = False
        self._initError: Exception | None = None

    def _getLoop(self) -> asyncio.AbstractEventLoop:
        if self._explicitLoop is not None:
            return self._explicitLoop
        return asyncio.get_running_loop()

    def _trackPending(self, fut: asyncio.Future[Any]) -> bool:
        """Track a future that waits for a response. Fail it if the connection is closed.

        Returns whether the request should be sent.
        """
        if self.closed:
            self._callSoon(fut, self._fail, fut, self._closedError())
            return False

        self._pendingFutures.add(fut)
        fut.add_done_callback(self._pendingFutures.discard)
        return True

    @staticmethod
    def _closedError() -> ConnectionError:
        # A new instance per future, as raising a shared instance in several tasks
        # chains up their tracebacks.
        return ConnectionError("The WebChannel connection was closed")

    @staticmethod
    def _callSoon(fut: asyncio.Future[Any], callback: Callable[..., None], *args: Any) -> None:
        """Run the callback in the loop of the future, unless that loop is closed."""
        # If the loop is closed, nobody can await the future any more
        with contextlib.suppress(RuntimeError):
            fut.get_loop().call_soon_threadsafe(callback, *args)

    def __await__(self) -> Generator[Any, None, None]:
        if self._initFuture is None:
            self._initFuture = self._getLoop().create_future()
            if self._initDone:
                self._settle(self._initFuture, self._initError)
        # All awaiters share the init future, so a cancelled await, e.g. after a
        # timeout, must not cancel it for the others.
        return asyncio.shield(self._initFuture).__await__()

    def _finishInit(self, error: Exception | None) -> None:
        # the first outcome wins, like for a future
        if self._initDone:
            return
        self._initDone = True
        self._initError = error
        if self._initFuture is not None:
            self._callSoon(self._initFuture, self._settle, self._initFuture, error)

    def initialized(self) -> None:
        try:
            super().initialized()
        except Exception as error:
            # Do not leave the awaiting caller hanging if the init callback fails
            self._finishInit(error)
            raise
        self._finishInit(None)

    def connection_closed(self) -> None:
        super().connection_closed()

        # Fail what still waits for a response that cannot arrive any more
        self._finishInit(self._closedError())
        for fut in list(self._pendingFutures):
            self._callSoon(fut, self._fail, fut, self._closedError())

    @classmethod
    def _settle(cls, fut: asyncio.Future[None], error: Exception | None) -> None:
        if error is None:
            cls._resolve(fut, None)
        else:
            cls._fail(fut, error)

    @staticmethod
    def _resolve(fut: asyncio.Future[Any], result: Any) -> None:
        # the future may be done already, e.g. cancelled by the caller
        if not fut.done():
            fut.set_result(result)

    @staticmethod
    def _fail(fut: asyncio.Future[Any], error: Exception) -> None:
        if not fut.done():
            fut.set_exception(error)
            # A lost connection is expected, so do not log the error of a future that
            # nobody awaits, e.g. of a call whose result is not needed. Awaiting the
            # future still raises.
            fut.exception()


class QWebChannelProtocol(QWebChannel, asyncio.Protocol):
    """A QWebChannel subclass implementing the asyncio.Protocol interface.

    For use with streaming transports. Assumes newline-delimited messages.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._buf = bytearray()

    def _try_process_messages(self) -> None:
        msgs = self._buf.split(b"\n")

        # This is either empty (if the last char is '\n') or holds an incomplete message.
        self._buf = msgs[-1]

        for msg in msgs[:-1]:
            if msg:
                self.message_received(msg.decode("utf-8"))

    def data_received(self, data: bytes) -> None:
        self._buf += data
        # Only scan the buffer once a message is complete, so that a large message
        # that arrives in many chunks is not split again for each chunk.
        if b"\n" in data:
            self._try_process_messages()

    def connection_lost(self, exc: Exception | None) -> None:
        self.connection_closed()

    def send(self, data: Message | str) -> None:
        if not isinstance(data, str):
            data = json.dumps(data)
        self.transport.write((data + "\n").encode("utf-8"))
