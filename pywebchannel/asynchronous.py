# Copyright (C) 2016 - 2018, Menlo Systems GmbH
#
# SPDX-License-Identifier: LGPL-3.0-only OR GPL-2.0-or-later

from __future__ import annotations

import asyncio
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
            loop = self._webChannel._loop
            fut = loop.create_future()

            def handleResponse(response: Any) -> None:
                def setResult() -> None:
                    # the caller may have cancelled the future in the meantime
                    if not fut.done():
                        fut.set_result(response)

                loop.call_soon_threadsafe(setResult)

            method(*args, handleResponse)
            return fut

        amethod.isQtMethod = True  # type: ignore[attr-defined]

        setattr(self, methodName, amethod)


class QWebChannel(PlainQWebChannel):
    """A QWebChannel that can be awaited to wait for the initialization to complete.

    Must be created from within a running event loop, or with an explicit `loop`.
    """

    QObjectType = QObject

    def __init__(
        self,
        initCallback: Callable[[PlainQWebChannel], None] | None = None,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        super().__init__(initCallback)

        self._loop = loop if loop is not None else asyncio.get_running_loop()
        self.__initialized_future: asyncio.Future[None] = self._loop.create_future()

    def __await__(self) -> Generator[Any, None, None]:
        return self.__initialized_future.__await__()

    def initialized(self) -> None:
        super().initialized()
        if not self.__initialized_future.done():
            self._loop.call_soon_threadsafe(self.__initialized_future.set_result, None)


class QWebChannelProtocol(QWebChannel, asyncio.Protocol):
    """A QWebChannel subclass implementing the asyncio.Protocol interface.

    For use with streaming transports. Assumes newline-delimited messages.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._buf = b""

    def _try_process_messages(self) -> None:
        msgs = self._buf.split(b"\n")

        # This is either empty (if the last char is '\n') or holds an incomplete message.
        self._buf = msgs[-1]

        for msg in msgs[:-1]:
            if msg:
                self.message_received(msg.decode("utf-8"))

    def data_received(self, data: bytes) -> None:
        self._buf += data
        self._try_process_messages()

    def connection_lost(self, exc: Exception | None) -> None:
        self.connection_closed()

    def send(self, data: Message | str) -> None:
        if not isinstance(data, str):
            data = json.dumps(data)
        self.transport.write((data + "\n").encode("utf-8"))
