# Copyright (C) 2016 - 2018, Menlo Systems GmbH
#
# SPDX-License-Identifier: LGPL-3.0-only OR GPL-2.0-or-later

from __future__ import annotations

import asyncio
import json
import threading
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
                loop.call_soon_threadsafe(QWebChannel._resolve, fut, response)

            if self._webChannel._trackPending(fut):
                try:
                    method(*args, handleResponse)
                except BaseException:
                    self._webChannel._untrackPending(fut)
                    raise
            return fut

        amethod.isQtMethod = True  # type: ignore[attr-defined]

        setattr(self, methodName, amethod)


class QWebChannel(PlainQWebChannel):
    """A QWebChannel that can be awaited to wait for the initialization to complete.

    The channel can be created without a running event loop. It uses the explicit `loop`
    if one is given, and otherwise the loop that runs when the channel is awaited or a
    remote method is called.
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

        # The init future only exists once someone awaits the channel. The outcome is
        # kept until then. The lock guards the init state, the pending futures and the
        # closed flag, because messages may be processed outside the thread of the loop.
        self._lock = threading.Lock()
        self._closed = False
        self._pendingFutures: set[asyncio.Future[Any]] = set()
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
        with self._lock:
            if not self._closed:
                self._pendingFutures.add(fut)
                fut.add_done_callback(self._untrackPending)
                return True
        fut.get_loop().call_soon_threadsafe(self._fail, fut, self._closedError())
        return False

    def _untrackPending(self, fut: asyncio.Future[Any]) -> None:
        with self._lock:
            self._pendingFutures.discard(fut)

    @staticmethod
    def _closedError() -> ConnectionError:
        return ConnectionError("The WebChannel connection was closed")

    def __await__(self) -> Generator[Any, None, None]:
        with self._lock:
            if self._initFuture is None:
                self._initFuture = self._getLoop().create_future()
                if self._initDone:
                    self._settle(self._initFuture, self._initError)
            fut = self._initFuture
        return fut.__await__()

    def _finishInit(self, error: Exception | None) -> None:
        with self._lock:
            # the first outcome wins, like for a future
            if self._initDone:
                return
            self._initDone = True
            self._initError = error
            fut = self._initFuture
        if fut is not None:
            fut.get_loop().call_soon_threadsafe(self._settle, fut, error)

    def connection_made(self, transport: Any) -> None:
        with self._lock:
            self._closed = False
            # a new connection initializes the channel again
            if self._initDone:
                self._initDone = False
                self._initError = None
                self._initFuture = None
        super().connection_made(transport)

    def initialized(self) -> None:
        super().initialized()
        self._finishInit(None)

    def connection_closed(self) -> None:
        super().connection_closed()

        # Fail what still waits for a response that cannot arrive any more
        error = self._closedError()
        self._finishInit(error)
        with self._lock:
            self._closed = True
            pending = list(self._pendingFutures)
        for fut in pending:
            fut.get_loop().call_soon_threadsafe(self._fail, fut, error)

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
