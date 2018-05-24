# -*- coding: utf-8 -*-

from __future__ import absolute_import, division, print_function

import json
import sys
import enum


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


class QWebChannel(object):

    initCallback = None

    # set to QObject further down
    QObjectType = None

    def __init__(self, initCallback=None):
        self.initCallback = initCallback

    def initialized(self):
        if (self.initCallback):
            self.initCallback(self)

    def connection_made(self, transport):
        self.transport = transport

        def callback(data):
            for objectName in data:
                self.QObjectType(objectName, data[objectName], self);

            # now unwrap properties, which might reference other registered objects
            for objectName in self.objects:
                self.objects[objectName]._unwrapProperties();

            self.initialized()

            self.exec_({"type": QWebChannelMessageTypes.idle});

        self.exec_({"type": QWebChannelMessageTypes.init}, callback)

    def send(self, data):
        if not isinstance(data, str):
            data = json.dumps(data)
        self.transport.send(data)

    def message_received(self, data):
        if isinstance(data, str):
            data = json.loads(data)

        if data["type"] == QWebChannelMessageTypes.signal:
            self.handleSignal(data);
        elif data["type"] == QWebChannelMessageTypes.response:
            self.handleResponse(data);
        elif data["type"] == QWebChannelMessageTypes.propertyUpdate:
            self.handle_propertyUpdate(data);
        else:
            print("invalid message received: ", data)

    execCallbacks = {};
    execId = 0;

    def exec_(self, data, callback=None):
        if not callback:
            # if no callback is given, send directly
            self.send(data);
            return

        if self.execId == sys.maxsize:
            # wrap
            self.execId = 0;

        if "id" in data:
            print("Cannot exec message with property id: " + json.dumps(data))
            return

        data["id"] = self.execId;
        self.execId = self.execId + 1
        self.execCallbacks[data["id"]] = callback;

        self.send(data);

    objects = {}

    def handleSignal(self, message):
        object = self.objects[message["object"]];
        if (object):
            object._signalEmitted(message["signal"], message.get("args", []));
        else:
            print("Unhandled signal: " + str(message["object"]) + "::" + str(message["signal"]))

    def handleResponse(self, message):
        if "id" not in message:
            print("Invalid response message received: ", json.dumps(message))
            return

        self.execCallbacks[message["id"]](message["data"])
        del self.execCallbacks[message["id"]];

    def handle_propertyUpdate(self, message):
        for data in message["data"]:
            qObject = self.objects[data["object"]];
            if qObject:
                qObject._propertyUpdate(data["signals"], data["properties"]);
            else:
                print("Unhandled property update: " + data["object"] + "::" + data["signal"])
        self.exec_({"type": QWebChannelMessageTypes.idle});

    def debug(self, message):
        self.send({"type": QWebChannelMessageTypes.debug, "data": message});


class QObject(object):

    def __init__(self, name, data, webChannel):
        self._id = name;
        self._webChannel = webChannel
        webChannel.objects[name] = self;

        # override the class so that we can dynamically add properties
        cls = self.__class__
        self.__class__ = type(cls.__name__, (cls,), {})

        # List of callbacks that get invoked upon signal emission
        self._objectSignals = {};

        # Cache of all properties, updated when a notify signal is emitted
        self._propertyCache = {};

        for method in data["methods"]:
            self._addMethod(method)

        for prop in data["properties"]:
            self._bindGetterSetter(prop)

        for signal in data["signals"]:
            self._addSignal(signal, False)

        for name, values in data.get("enums", {}).items():
            setattr(self, name, enum.IntEnum(name, values))


    def _unwrapQObject(self, response):
        if isinstance(response, list):
            # support list of objects
            return [ self._unwrapQObject(object) for object in response ]

        if (not isinstance(response, dict)
                or "__QObject*__" not in response
                or "id" not in response["id"]):
            return response;

        objectId = response["id"];
        if objectId in self._webChannel.objects:
            return self._webChannel.objects[objectId];

        if "data" not in response:
            print("Cannot unwrap unknown QObject " + objectId + " without data.")
            return

        qObject = QObject( objectId, response["data"], self._webChannel )

        def destroyedFunction():
            if self._webChannel.objects[objectId] == qObject:
                del self._webChannel.objects[objectId];
                # reset the now deleted QObject to an empty {} object
                # just assigning {} though would not have the desired effect, but the
                # below also ensures all external references will see the empty map
                # NOTE: this detour is necessary to workaround QTBUG-40021

                # Not needed in Python, I guess:
#                propertyNames = [];
#                for (var propertyName in qObject) {
#                    propertyNames.push(propertyName);
#                }
#                for (var idx in propertyNames) {
#                    delete qObject[propertyNames[idx]];

        qObject.destroyed.connect(destroyedFunction)

        # here we are already initialized, and thus must directly unwrap the properties
        qObject._unwrapProperties();
        return qObject;

    def _unwrapProperties(self):
        for propertyIdx in range(len(self._propertyCache)):
            self._propertyCache[propertyIdx] = self._unwrapQObject(self._propertyCache[propertyIdx])

    def _addSignal(self, signalData, isPropertyNotifySignal):
        signalName = signalData[0];
        signalIndex = signalData[1];

        setattr(self, signalName, Signal(self, signalIndex, signalName, isPropertyNotifySignal))

    def _invokeSignalCallbacks(self, signalName, signalArgs):
        """Invokes all callbacks for the given signalname. Also works for property notify callbacks."""
        try:
            signalName = int(signalName)
        except ValueError:
            pass

        if signalName not in self._objectSignals:
            return

        for callback in self._objectSignals[signalName]:
            callback(*signalArgs)

    def _propertyUpdate(self, signals, propertyMap):
        # update property cache
        for propertyIndex in propertyMap:
            propertyValue = propertyMap[propertyIndex]
            self._propertyCache[int(propertyIndex)] = propertyValue

        for signalName in signals:
            # Invoke all callbacks, as _signalEmitted() does not. This ensures the
            # property cache is updated before the callbacks are invoked.
            self._invokeSignalCallbacks(signalName, signals[signalName]);

    def _signalEmitted(self, signalName, signalArgs):
        self._invokeSignalCallbacks(signalName, signalArgs)

    def _addMethod(self, methodData):
        methodName = methodData[0];
        methodIdx = methodData[1];

        def method(*arguments):
            args = []
            callback = None
            for arg in arguments:
                if callable(arg):
                    callback = arg
                else:
                    args.append(arg)

            def responseCallback(response):
                result = self._unwrapQObject(response)
                if callback:
                    callback(result)

            self._webChannel.exec_({
                "type": QWebChannelMessageTypes.invokeMethod,
                "object": self._id,
                "method": methodIdx,
                "args": args
            }, responseCallback);

        method.isQtMethod = True

        setattr(self, methodName, method)

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
                notifySignalData[0] = propertyName + "Changed";
            self._addSignal(notifySignalData, True)

        def getter(self):
            propertyValue = self._propertyCache[propertyIndex];
            if propertyValue is None:
                # This shouldn't happen
                print("Undefined value in property cache for property \"" + propertyName + "\" in object " + self._id)

            return propertyValue;

        def setter(self, value):
            if value is None:
                print("Property setter for " + propertyName + " called with 'None' value!")
                return

            self._propertyCache[propertyIndex] = value;
            self._webChannel.exec_({
                "type": QWebChannelMessageTypes.setProperty,
                "object": self._id,
                "property": propertyIndex,
                "value": value
            });

        setattr(self.__class__, propertyName, property(getter, setter))


class Signal(object):

    def __init__(self, qObject, signalIndex, signalName, isPropertyNotifySignal):
        self._qObject = qObject
        self._signalIndex = signalIndex
        self._signalName = signalName
        self.isPropertyNotifySignal = isPropertyNotifySignal

    def connect(self, callback):
        if not callable(callback):
            print("Bad callback given to connect to signal " + self._signalName)
            return

        if self._signalIndex not in self._qObject._objectSignals:
            self._qObject._objectSignals[self._signalIndex] = []
        self._qObject._objectSignals[self._signalIndex].append(callback);

        if not self.isPropertyNotifySignal and self._signalName != "destroyed":
            # only required for "pure" signals, handled separately for properties in _propertyUpdate
            # also note that we always get notified about the destroyed signal
            self._qObject._webChannel.exec_({
                "type": QWebChannelMessageTypes.connectToSignal,
                "object": self._qObject._id,
                "signal": self._signalIndex
            })

    def disconnect(self, callback):
        if not callable(callback):
            print("Bad callback given to disconnect from signal " + self._signalName)
            return

        if self._signalIndex not in self._qObject._objectSignals:
            self._qObject._objectSignals[self._signalIndex] = []

        if callback not in self._qObject._objectSignals[self._signalIndex]:
            print("Cannot find connection of signal " + self._signalName + " to " + str(callback))
            return

        self._qObject._objectSignals[self._signalIndex].remove(callback)

        if not self.isPropertyNotifySignal and len(self._qObject._objectSignals[self._signalIndex]) == 0:
            # only required for "pure" signals, handled separately for properties in _propertyUpdate
            self._qObject._webChannel.exec_({
                "type": QWebChannelMessageTypes.disconnectFromSignal,
                "object": self._qObject._id,
                "signal": self._signalIndex
            })


QWebChannel.QObjectType = QObject
