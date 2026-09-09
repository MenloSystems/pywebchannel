# Copyright (C) 2016 - 2018, Menlo Systems GmbH
# Copyright (C) 2016 The Qt Company Ltd.
# Copyright (C) 2016 Klarälvdalens Datakonsult AB, a KDAB Group company
#
# SPDX-License-Identifier: LGPL-3.0-only OR GPL-2.0-or-later

import contextlib
import enum
import inspect
import json
import logging
import sys

logger = logging.getLogger(__name__)


class QWebChannelMessageTypes(enum.IntEnum):
    signal = 1
    propertyUpdate = 2
    init = 3
    idle = 4
    debug = 5
    invokeMethod = 6
    connectToSignal = 7
    disconnectFromSignal = 8
    setProperty = 9
    response = 10


class QObject:
    def __init__(self, name, data, webChannel):
        self._id = name
        self._webChannel = webChannel
        webChannel.objects[name] = self

        # override the class so that we can dynamically add properties
        cls = self.__class__
        self.__class__ = type(f"{cls.__name__}-{name}", (cls,), {})
        self.__class__.__doc__ = f"Interface for remote object {name}"

        # List of callbacks that get invoked upon signal emission
        self._objectSignals = {}

        # Cache of all properties, updated when a notify signal is emitted
        self._propertyCache = {}

        for method in data["methods"]:
            self._addMethod(method)

        for prop in data["properties"]:
            self._bindGetterSetter(prop)

        for signal in data["signals"]:
            self._addSignal(signal)

        for enumName, values in data.get("enums", {}).items():
            setattr(self.__class__, enumName, enum.IntEnum(enumName, values))

    def __dir__(self):
        def keep(member):
            obj = inspect.getattr_static(self, member)
            return (
                (hasattr(obj, "isQtMethod") or isinstance(obj, SignalDescriptor))
                and "(" not in member
            ) or isinstance(obj, (property, enum.EnumMeta))

        return [x for x in super().__dir__() if keep(x)]

    def _unwrapQObject(self, response):
        if isinstance(response, list):
            # support list of objects
            return [self._unwrapQObject(entry) for entry in response]

        if not isinstance(response, dict):
            return response

        # Support QObjects as values in a map
        if "__QObject*__" not in response or "id" not in response:
            return {k: self._unwrapQObject(v) for k, v in response.items()}

        objectId = response["id"]
        if objectId in self._webChannel.objects:
            return self._webChannel.objects[objectId]

        if "data" not in response:
            logger.warning("Cannot unwrap unknown QObject %s without data.", objectId)
            return

        qObject = self._webChannel.QObjectType(objectId, response["data"], self._webChannel)

        def destroyedFunction(*args):
            if self._webChannel.objects[objectId] is qObject:
                del self._webChannel.objects[objectId]
                # reset the now deleted QObject to an empty {} object
                # just assigning {} though would not have the desired effect, but the
                # below also ensures all external references will see the empty map
                # NOTE: this detour is necessary to workaround QTBUG-40021

        qObject.destroyed.connect(destroyedFunction)

        # here we are already initialized, and thus must directly unwrap the properties
        qObject._unwrapProperties()
        return qObject

    def _unwrapProperties(self):
        for propertyIdx in list(self._propertyCache):
            self._propertyCache[propertyIdx] = self._unwrapQObject(self._propertyCache[propertyIdx])

    def _addSignal(self, signalData, propertyName=None):
        signalName, signalIndex = signalData[0], signalData[1]

        setattr(self.__class__, signalName, SignalDescriptor(signalIndex, signalName, propertyName))

    def _invokeSignalCallbacks(self, signalName, signalArgs):
        """Invoke all callbacks for the given signal. Also works for property notify signals."""
        with contextlib.suppress(ValueError):
            signalName = int(signalName)

        # iterate over a copy: a callback may disconnect itself
        for callback in list(self._objectSignals.get(signalName, [])):
            callback(*signalArgs)

    def _propertyUpdate(self, signals, propertyMap):
        # update property cache
        for propertyIndex, propertyValue in propertyMap.items():
            self._propertyCache[int(propertyIndex)] = self._unwrapQObject(propertyValue)

        for signalName, signalArgs in signals.items():
            # Invoke all callbacks, as _signalEmitted() does not. This ensures the
            # property cache is updated before the callbacks are invoked.
            self._invokeSignalCallbacks(signalName, signalArgs)

    def _signalEmitted(self, signalName, signalArgs):
        self._invokeSignalCallbacks(signalName, signalArgs)

    def _addMethod(self, methodData):
        methodName, methodIdx = methodData[0], methodData[1]

        def method(self, *arguments):
            args = []
            callback = None
            for arg in arguments:
                if callable(arg):
                    callback = arg
                elif isinstance(arg, QObject) and arg._id in self._webChannel.objects:
                    args.append({"id": arg._id})
                else:
                    args.append(arg)

            def responseCallback(response):
                result = self._unwrapQObject(response)
                if callback:
                    callback(result)

            self._webChannel.exec_(
                {
                    "type": QWebChannelMessageTypes.invokeMethod,
                    "object": self._id,
                    "method": methodIdx,
                    "args": args,
                },
                responseCallback,
            )

        method.isQtMethod = True

        setattr(self.__class__, methodName, method)

    def _bindGetterSetter(self, propertyInfo):
        propertyIndex, propertyName, notifySignalData, propertyValue = propertyInfo
        propertyIndex = int(propertyIndex)

        # initialize property cache with current value
        # NOTE: if this is an object, it is not directly unwrapped as it might
        # reference other QObject that we do not know yet
        self._propertyCache[propertyIndex] = propertyValue

        if notifySignalData:
            if notifySignalData[0] == 1:
                # signal name is optimized away, reconstruct the actual name
                notifySignalData[0] = f"{propertyName}Changed"
            self._addSignal(notifySignalData, propertyName)

        def getter(self):
            return self._propertyCache[propertyIndex]

        def setter(self, value):
            if value is None:
                logger.warning("Property setter for %s called with 'None' value!", propertyName)
                return

            self._propertyCache[propertyIndex] = value

            valueToSend = value
            if isinstance(value, QObject) and value._id in self._webChannel.objects:
                valueToSend = {"id": value._id}

            self._webChannel.exec_(
                {
                    "type": QWebChannelMessageTypes.setProperty,
                    "object": self._id,
                    "property": propertyIndex,
                    "value": valueToSend,
                }
            )

        setattr(self.__class__, propertyName, property(getter, setter, doc="Property"))


class SignalDescriptor:
    def __init__(self, signalIndex, signalName, propertyName):
        self.signalIndex = signalIndex
        self.signalName = signalName
        self.propertyName = propertyName

        if propertyName is not None:
            self.__doc__ = f"Change notification signal for property {propertyName}"
        else:
            self.__doc__ = "Signal"

    def __get__(self, obj, objtype=None):
        attrname = "_signal_" + self.signalName
        try:
            return getattr(obj, attrname)
        except AttributeError:
            signal = Signal(obj, self.signalIndex, self.signalName, self.propertyName is not None)
            setattr(obj, attrname, signal)
            return signal

    def __set__(self, obj, value):
        raise AttributeError(f"Cannot assign to signal {self.signalName}")


class Signal:
    def __init__(self, qObject, signalIndex, signalName, isPropertyNotifySignal):
        self._qObject = qObject
        self._signalIndex = signalIndex
        self._signalName = signalName
        self.isPropertyNotifySignal = isPropertyNotifySignal

    def connect(self, callback):
        if not callable(callback):
            logger.warning("Bad callback given to connect to signal %s", self._signalName)
            return

        self._qObject._objectSignals.setdefault(self._signalIndex, []).append(callback)

        if not self.isPropertyNotifySignal and self._signalName != "destroyed":
            # only required for "pure" signals, handled separately for properties in _propertyUpdate
            # also note that we always get notified about the destroyed signal
            self._qObject._webChannel.exec_(
                {
                    "type": QWebChannelMessageTypes.connectToSignal,
                    "object": self._qObject._id,
                    "signal": self._signalIndex,
                }
            )

    def disconnect(self, callback):
        if not callable(callback):
            logger.warning("Bad callback given to disconnect from signal %s", self._signalName)
            return

        callbacks = self._qObject._objectSignals.setdefault(self._signalIndex, [])

        if callback not in callbacks:
            logger.warning("Cannot find connection of signal %s to %s", self._signalName, callback)
            return

        callbacks.remove(callback)

        if not self.isPropertyNotifySignal and not callbacks:
            # only required for "pure" signals, handled separately for properties in _propertyUpdate
            self._qObject._webChannel.exec_(
                {
                    "type": QWebChannelMessageTypes.disconnectFromSignal,
                    "object": self._qObject._id,
                    "signal": self._signalIndex,
                }
            )


class QWebChannel:
    QObjectType = QObject

    def __init__(self, initCallback=None):
        self.initCallback = initCallback
        self.__initialized = False
        self.objects = {}
        self.execCallbacks = {}
        self.execId = 0
        self.transport = None

    def initialized(self):
        self.__initialized = True
        if self.initCallback:
            self.initCallback(self)

    def connection_made(self, transport):
        self.transport = transport

        def callback(data):
            for objectName, objectData in data.items():
                self.QObjectType(objectName, objectData, self)

            # now unwrap properties, which might reference other registered objects
            for qObject in list(self.objects.values()):
                qObject._unwrapProperties()

            self.initialized()

            if self.__initialized:
                self.exec_({"type": QWebChannelMessageTypes.idle})

        self.exec_({"type": QWebChannelMessageTypes.init}, callback)

    def connection_closed(self):
        self.__initialized = False

    def send(self, data):
        if not isinstance(data, str):
            data = json.dumps(data)
        self.transport.send(data)

    def message_received(self, data):
        if isinstance(data, (str, bytes)):
            data = json.loads(data)

        if data["type"] == QWebChannelMessageTypes.response:
            self.handleResponse(data)
            return

        if not self.__initialized:
            return  # All objects have to be created first!

        if data["type"] == QWebChannelMessageTypes.signal:
            self.handleSignal(data)
        elif data["type"] == QWebChannelMessageTypes.propertyUpdate:
            self.handle_propertyUpdate(data)
        else:
            logger.warning("Invalid message received: %s", data)

    def exec_(self, data, callback=None):
        if not callback:
            # if no callback is given, send directly
            self.send(data)
            return

        if self.execId == sys.maxsize:
            # wrap
            self.execId = 0

        if "id" in data:
            logger.error("Cannot exec message with property id: %s", json.dumps(data))
            return

        data["id"] = self.execId
        self.execId += 1
        self.execCallbacks[data["id"]] = callback

        self.send(data)

    def handleSignal(self, message):
        qObject = self.objects.get(message["object"])
        if qObject is not None:
            qObject._signalEmitted(message["signal"], message.get("args", []))
        else:
            logger.warning("Unhandled signal: %s::%s", message.get("object"), message.get("signal"))

    def handleResponse(self, message):
        if "id" not in message:
            logger.warning("Invalid response message received: %s", json.dumps(message))
            return

        callback = self.execCallbacks.pop(message["id"], None)
        if callback is None:
            logger.warning("Received response for unknown id %s", message["id"])
            return

        callback(message["data"])

    def handle_propertyUpdate(self, message):
        for data in message["data"]:
            qObject = self.objects.get(data["object"])
            if qObject is not None:
                qObject._propertyUpdate(data["signals"], data["properties"])
            else:
                logger.warning("Unhandled property update for %s", data.get("object"))
        if self.__initialized:
            self.exec_({"type": QWebChannelMessageTypes.idle})

    def debug(self, message):
        self.send({"type": QWebChannelMessageTypes.debug, "data": message})
