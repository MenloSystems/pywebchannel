from .qwebchannel import (QObject as PlainQObject,
                          QWebChannel as PlainQWebChannel)
import asyncio
import json


class QObject(PlainQObject):
    """A QObject whose remote methods return awaitables instead of taking a callback."""

    def _addMethod(self, methodData):
        super()._addMethod(methodData)

        methodName = methodData[0]
        method = getattr(self, methodName)

        def amethod(*args):
            fut = self._webChannel._loop.create_future()

            def handleResponse(*args):
                self._webChannel._loop.call_soon_threadsafe(fut.set_result, *args)

            method(*args, handleResponse)
            return fut

        amethod.isQtMethod = True
        setattr(self, methodName, amethod)


class QWebChannel(PlainQWebChannel):
    """A QWebChannel that can be awaited to wait for the initialization to complete.

    Must be created from within a running event loop, or with an explicit `loop`.
    """

    QObjectType = QObject

    def __init__(self, initCallback=None, *, loop=None):
        super().__init__(initCallback)

        self._loop = loop if loop is not None else asyncio.get_running_loop()
        self.__initialized_future = self._loop.create_future()

    def __await__(self):
        return self.__initialized_future.__await__()

    def initialized(self):
        super().initialized()
        self._loop.call_soon_threadsafe(self.__initialized_future.set_result, None)


class QWebChannelProtocol(QWebChannel, asyncio.Protocol):
    """A QWebChannel subclass implementing the asyncio.Protocol interface.

    For use with streaming transports. Assumes newline-delimited messages.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._buf = b''

    def _try_process_messages(self):
        msgs = self._buf.split(b'\n')

        # This is either empty (if the last char is '\n') or holds an incomplete message.
        self._buf = msgs[-1]

        for msg in msgs[:-1]:
            if msg:
                self.message_received(msg.decode('utf-8'))

    def data_received(self, data):
        self._buf += data
        self._try_process_messages()

    def connection_lost(self, exc):
        self.connection_closed()

    def send(self, data):
        if not isinstance(data, str):
            data = json.dumps(data)
        self.transport.write((data + '\n').encode('utf-8'))
