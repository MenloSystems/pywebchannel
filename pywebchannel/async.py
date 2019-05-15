# -*- coding: utf-8 -*-

from .qwebchannel import (QObject as PlainQObject,
                          QWebChannel as PlainQWebChannel)
import asyncio
import inspect
import json


class QObject(PlainQObject):

    def _addMethod(self, methodData):
        super()._addMethod(methodData)

        methodName = methodData[0];
        method = getattr(self, methodName)

        def amethod(*args):
            fut = self._webChannel._loop.create_future()

            def handleResponse(*args):
                self._webChannel._loop.call_soon_threadsafe(fut.set_result, *args)

            method(*args, handleResponse)
            return fut

        setattr(amethod, 'isQtMethod', True)
        setattr(self, methodName, amethod)


class QWebChannel(PlainQWebChannel):

    QObjectType = QObject

    def __init__(self, *args, loop=None, **kwargs):
        super().__init__(*args, **kwargs)

        if loop is None:
            loop = asyncio.get_event_loop()
        self._loop = loop

        self.__initialized_future = self._loop.create_future()

    def __await__(self):
        return self.__initialized_future.__await__()

    def initialized(self):
        super().initialized()
        self._loop.call_soon_threadsafe(self.__initialized_future.set_result, None)


class QWebChannelProtocol(QWebChannel):
    '''A QWebChannel subclass implementing the asyncio.Protocol interface.

    For use with streaming transports. Assumes newline-delimited messages.'''

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._buf = b''

    def _try_process_messages(self):
        msgs = self._buf.split(b'\n')

        # This is either empty (if last char is '\n') or contains an incomplete message, so we're good.
        self._buf = msgs[-1]

        # Strip of incomplete or empty message
        msgs = msgs[:-1]

        for msg in msgs:
            self.message_received(msg.decode('utf-8'))

    def data_received(self, data):
        self._buf += data
        self._try_process_messages()

    def send(self, data):
        if not isinstance(data, str):
            data = json.dumps(data)
        self.transport.write((data + '\n').encode('utf-8'))
