import gevent
import socket
import requests
from json import dumps, loads
from time import sleep
from websocket import WebSocketConnectionClosedException, create_connection


PROTOCOL = 1  # socket.io protocol version


class SocketIOError(Exception):
    pass

class SocketIOConnectionError(SocketIOError):
    pass

class SocketIOPacketError(SocketIOError):
    pass

HEARTBEAT_WAIT = 15

class BaseNamespace(object):  # pragma: no cover
    'Define socket.io behavior'

    def __init__(self, _socketIO, path):
        self.io = _socketIO
        self._path = path
        self.callbacks = dict()
        self.initialize()

    def initialize(self):
        'Initialize custom variables here; you can override this method'
        pass

    def on_connect(self):
        'Called when socket is connecting; you can override this method'
        pass

    def on_disconnect(self, exception=None):
        'Called when socket is disconnecting; you can override this method'
        pass

    def on_error(self, reason, advice):
        'Called when server sends an error; you can override this method'
        print '[Error] %s' % advice

    def on_message(self, data):
        'Called when server sends a message; you can override this method'
        print '[Message] %s' % data

    def on_event(self, event, *args):
        """
        Called when server emits an event; you can override this method.
        Called only if the program cannot find a more specific event handler,
        such as one defined by namespace.on('my_event', my_function).
        """
        callback, args = find_callback(args)
        arguments = [repr(_) for _ in args]
        if callback:
            arguments.append('callback(*args)')
            callback(*args)
        print '[Event] %s(%s)' % (event, ', '.join(arguments))

    def on_open(self, *args):
        print '[Open]', args

    def on_close(self, *args):
        print '[Close]', args

    def on_retry(self, *args):
        print '[Retry]', args

    def on_reconnect(self, *args):
        print '[Reconnect]', args

    def message(self, data='', callback=None):
        self.io.message(data, callback, path=self._path)

    def emit(self, event, *args, **kw):
        kw['path'] = self._path
        self.io.emit(event, *args, **kw)

    def on(self, event, callback):
        'Define a callback to handle a custom event emitted by the server'
        self.callbacks[event] = callback

    def _get_event_callback(self, event):
        # Check callbacks defined by on()
        try:
            return self.callbacks[event]
        except KeyError:
            pass
        # Check callbacks defined explicitly or use on_event()
        callback = lambda *args: self.on_event(event, *args)
        return getattr(self, 'on_' + event.replace(' ', '_'), callback)


class SocketIO(object):
    def __init__(self, host, port, Namespace=BaseNamespace, secure=False, headers=None, proxies=None):
        """
        Create a socket.io client that connects to a socket.io server
        at the specified host and port.  Set secure=True to use HTTPS / WSS.

        SocketIO('localhost', 8000, secure=True,
            proxies={'https': 'https://proxy.example.com:8080'})
        """
        self.io = _SocketIO(self, host, port, secure, headers, proxies)

        self.namespaces = dict()
        self.add_namespace(Namespace)

    def __del__(self):
        self.disconnect()

    # connect/namespaces/disconnect

    @property
    def connected(self):
        return self.io.connected

    def add_namespace(self, Namespace, path=''):
        if path in self.namespaces:
            raise RuntimeError('namespace on path "{}"" already exists'.format(path))
        if not self.connected:
            self.io.connect()
        if path:
            self.send_packet(1, path)
        self.namespaces[path] = Namespace(self.io, path)
        return self.namespaces[path]

    def get_namespace(self, path=''):
        return self.namespaces[path]

    def remove_namespace(self, path='', exception=None):
        if self.connected:
            try:
                self.io.send_packet(0, path) # disconnect from path
            except:
                pass
        self.namespaces[path].on_disconnect(exception)
        del self.namespaces[path]
        if not self.namespaces:
            self.io.disconnect()

    def disconnect(self, exception=None):
        for path in self.namespaces.keys():
            try:
                self.remove_namespace(path, exception)
            except:
                pass
        self.namespaces = dict()

    # messages/events

    def on(self, event, callback, path=''):
        return self.get_namespace(path).on(event, callback)

    def message(self, data='', callback=None, path=''):
        self.io.message(data, callback, path)

    def emit(self, event, *args, **kw):
        self.io.emit(event, *args, **kw)

    def wait(self, seconds=None):
        if seconds:
            self._listenerThread.wait(seconds)
        else:
            try:
                while self.connected:
                    sleep(1)
            except KeyboardInterrupt:
                pass

    def wait_for_callbacks(self, seconds=None):
        self._listenerThread.wait_for_callbacks(seconds)


class _SocketIO(object):
    'Low-level interface to remove cyclic references in child threads'

    def __init__(self, parent, host, port, secure, headers, proxies):
        self.parent = parent
        self.headers = headers
        self.proxies = proxies
        self.connection = None
        self.heartbeat_timeout_handler = None

        base_url = '%s:%d/socket.io/%s' % (host, port, PROTOCOL)
        self.http_url = '%s://%s' % ('https' if secure else 'http', base_url)
        self.socket_url = '%s://%s/websocket/' % ('wss' if secure else 'ws', base_url)

    def __del__(self):
        self.disconnect(close=False)

    # connect/disconnect

    @property
    def connected(self):
        return self.connection.connected if self.connection else False

    def connect(self):
        if self.connected:
            raise RuntimeError('already connected')

        response = requests.get(self.http_url, headers=self.headers, proxies=self.proxies)
        response.raise_for_status()

        parts = response.text.split(':')
        session_id = parts[0]
        self.heartbeat_timeout = int(parts[1])

        supported_transports = parts[3].split(',')
        if 'websocket' not in supported_transports:
            raise SocketIOError('Could not parse handshake')

        self.message_id = 0
        self.message_callbacks = dict()

        self.connection = create_connection(self.socket_url + session_id)

        self.heartbeat_timeout += HEARTBEAT_WAIT
        self.reset_heartbeat_timeout()

        self.listener = gevent.spawn(self.listener)

    def disconnect(self):
        if not self.connected:
            raise RuntimeError('not connected')
        if self.heartbeat:
            self.heartbeat.kill()
            self.heartbeat = None
        if self.heartbeat_timeout_handler:
            self.heartbeat_timeout_handler.kill()
            self.heartbeat_timeout_handler = None
        if self.listener:
            self.listener.kill()
            self.listener = None
        self.connection.close()
        self.connection = None

    # heartbeat

    def reset_heartbeat_timeout(self):
        if self.heartbeat_timeout_handler:
            self.heartbeat_timeout_handler.kill()
        self.heartbeat_timeout_handler = gevent.spawn_later(self.heartbeat_timeout, self.heartbeat_timed_out)

    def heartbeat_timed_out(self):
        self.heartbeat_timeout_handler = None
        try:
            raise SocketIOConnectionError('heartbeat timed out')
        except BaseException as e:
            self.parent.disconnect(e)

    # messages/events

    def message(self, data, callback, path):
        if isinstance(data, basestring):
            code = 3
        else:
            code = 4
            data = dumps(data, ensure_ascii=False)
        self.send_packet(code, path, data, callback)

    def emit(self, event, *args, **kw):
        callback, args = find_callback(args, kw)
        data = dumps(dict(name=event, args=args), ensure_ascii=False)
        path = kw.get('path', '')
        self.send_packet(5, path, data, callback)

    def ack(self, packet_id, *args):
        packet_id = packet_id.rstrip('+')
        data = '%s+%s' % (packet_id, dumps(args, ensure_ascii=False)) if args else packet_id
        self.send_packet(6, data=data)

    # callbacks by message id

    def set_message_callback(self, callback):
        'Set callback that will be called after receiving an acknowledgment'
        self.message_id += 1
        self.message_callbacks[self.message_id] = callback
        return '%s+' % self.message_id

    def get_message_callback(self, message_id):
        try:
            callback = self.message_callbacks[message_id]
            del self.message_callbacks[message_id]
            return callback
        except KeyError:
            return

    @property
    def has_message_callback(self):
        return True if self.message_callbacks else False

    # main receive/send functions

    def recv_packet(self):
        try:
            packet = self.connection.recv()
        except WebSocketConnectionClosedException:
            raise SocketIOConnectionError('Lost connection (Connection closed)')
        except socket.timeout:
            raise SocketIOConnectionError('Lost connection (Connection timed out)')
        except socket.error:
            raise SocketIOConnectionError('Lost connection')
        try:
            parts = packet.split(':', 3)
        except AttributeError:
            raise SocketIOPacketError('Received invalid packet (%s)' % packet)

        count = len(parts)
        code, packet_id, path, data = None, None, None, None
        if 4 == count:
            code, packet_id, path, data = parts
        elif 3 == count:
            code, packet_id, path = parts
        elif 1 == count:
            code = parts[0]
        return code, packet_id, path, data

    def send_packet(self, code, path='', data='', callback=None):
        packet_id = self.set_message_callback(callback) if callback else ''
        parts = [str(code), packet_id, path, data]
        packet = ':'.join(parts)
        try:
            self.connection.send(packet)
        except BaseException as e:
            self.parent.disconnect(e)
            return

    # listener functions

    def get_ack_callback(self, packet_id):
        return lambda *args: self.ack(packet_id, *args)

    def listener(self):
        while True:
            #from ..api import proto
            #print "........", proto.unpack_message()
            try:
                try:
                    code, packet_id, path, data = self.recv_packet()
                except BaseException as e:
                    gevent.spawn(self.parent.disconnect, e)
                    return
                try:
                    namespace = self.parent.namespaces[path]
                except KeyError as e:
                    #print 'Received unexpected path (%s)' % path
                    # handle this as error
                    self.parent.disconnect(e)
                    continue
                try:
                    func = {
                        '0': self.on_disconnect,
                        '1': self.on_connect,
                        '2': self.on_heartbeat,
                        '3': self.on_message,
                        '4': self.on_json,
                        '5': self.on_event,
                        '6': self.on_ack,
                        '7': self.on_error,
                    }[code]
                except KeyError:
                    print 'Received unexpected code (%s)' % code
                    continue
                func(packet_id, namespace._get_event_callback, data)
            except BaseException as e:
                gevent.spawn(self.parent.disconnect, e)
                return

    def on_connect(self, packet_id, get_event_callback, data):
        get_event_callback('connect')()

    def on_disconnect(self, packet_id, get_event_callback, data):
        gevent.spawn(self.disconnect)

    def on_heartbeat(self, packet_id, get_event_callback, data):
        self.send_packet(2)
        self.reset_heartbeat_timeout()

    def on_message(self, packet_id, get_event_callback, data):
        args = [data]
        if packet_id:
            args.append(self.get_ack_callback(packet_id))
        get_event_callback('message')(*args)

    def on_json(self, packet_id, get_event_callback, data):
        args = [loads(data)]
        if packet_id:
            args.append(self.get_ack_callback(packet_id))
        get_event_callback('message')(*args)

    def on_event(self, packet_id, get_event_callback, data):
        value_by_name = loads(data)
        event = value_by_name['name']
        args = value_by_name.get('args', [])
        if packet_id:
            args.append(self.get_ack_callback(packet_id))
        get_event_callback(event)(*args)

    def on_ack(self, packet_id, get_event_callback, data):
        dataParts = data.split('+', 1)
        message_id = int(dataParts[0])
        args = loads(dataParts[1]) if len(dataParts) > 1 else []
        callback = self.get_message_callback(message_id)
        if not callback:
            return
        callback(*args)
        if not self.has_message_callback:
            self.ready.set()

    def on_error(self, packet_id, get_event_callback, data):
        reason, advice = data.split('+', 1)
        get_event_callback('error')(reason, advice)


def find_callback(args, kw=None):
    'Return callback whether passed as a last argument or as a keyword'
    if args and callable(args[-1]):
        return args[-1], args[:-1]
    try:
        return kw['callback'], args
    except (KeyError, TypeError):
        return None, args
