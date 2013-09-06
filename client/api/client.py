#!/usr/bin/env python
# encoding: utf-8
"""Copyright (C) 2013 COLDWELL AG

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <http://www.gnu.org/licenses/>.
"""

import sys
import time
import base64
import socket
import gevent
import traceback
import random
import platform
from itertools import cycle

from gevent import Timeout
from gevent.lock import Semaphore
from gevent.event import Event, AsyncResult

from . import proto
from .. import logger, event, login, interface, settings, plugintools, input, proxy
from ..config import globalconfig
from ..contrib.socketioclient import BaseNamespace, SocketIO

log = logger.get("api.client")

config = globalconfig.new('api').new('client')
config.default('show_error_dialog', True, bool)

class APIClient(BaseNamespace, plugintools.GreenletObject):
    #nodes = ["ws-{}.download.am".format(i) for i in range(3, 4)]
    nodes = ['ws.download.am']
    random.shuffle(nodes)
    node_cycler = cycle(nodes)
    node_port = 443
    change_node = False # change node if asked to, may be overridden by --testbackend for internal testing

    def __init__(self):
        plugintools.GreenletObject.__init__(self)

        self.io = None
        self.next_node = None
        self.greenlet = None
        self.lock = Semaphore()

        self.login_results = dict()
        self.connected_event = Event()
        self.disconnected_event = Event()

        self.connect_retry = 0
        self.connect_retry_last_msgbox = 0
        self.connect_error_dialog = None

        self.connection_states = None
        
    def __call__(self, socketio, path):
        BaseNamespace.__init__(self, socketio, path)
        return self

    def connect(self):
        try:
            if self.io:
                self.io.disconnect()

            node = self.node_cycler.next()
            node_port = self.node_port
                
            log.info('connecting to {}'.format(node))
            self.io = SocketIO(node, node_port, self, secure=True)

            all_events = ["disconnect", "reconnect", "open", "close", "error", "retry", "message"]
            for e in all_events:
                self.io.on(e, getattr(self, "on_" + e))
        except BaseException as e:
            traceback.print_exc()
            return e

    def is_connected(self):
        return True if self.connected_event.is_set() and not self.disconnected_event.is_set() else False

    def wait_connected(self):
        self.connected_event.wait()

    def close(self):
        if self.greenlet and gevent.getcurrent() != self.greenlet:
            self.greenlet.kill()
        if self.io:
            io = self.io
            self.io = None
            try:
                io.disconnect()
            except:
                pass
            log.info('closed api connection')
        self.disconnected_event.set()

    def on_connect(self, *args, **kwargs):
        log.debug('connected')
    
    def on_disconnect(self, exception):
        if self.io:
            if exception:
                log.warning('disconnected with error: {}'.format(exception))
            else:
                log.debug('disconnected')
            self.close()
        
    def on_reconnect(self, *args):
        log.warning("unhandled event: RECONNECT: {}".format(args))

    def on_open(self, *args):
        log.warning("unhandled event: OPEN: {}".format(args))

    def on_close(self, *args):
        log.warning("unhandled event: CLOSE: {}".format(args))

    def on_error(self, reason, advice):
        log.warning("unhandled event: ERROR: reason: {}, advise: {}".format(reason, advice))

    def on_retry(self, *args):
        log.warning("unhandled event: RETRY: {}".format(args))

    def on_(self, *args):
        log.warning("unhandled event: ON_: {}".format(args))

    def send_message(self, message):
        with self.lock:
            #print ">>> SEND CLIENT", message['flags'], message.get('command'), message['payload']
            self.emit("message", message)

    def send(self, type, command=None, in_response_to=None, payload=None, channel=None, encrypt=True, emit=None):
        if type == 'frontend':
            if not self.connected_event.is_set():
                return

        message = proto.pack_message(type, command, in_response_to, payload, channel=channel, encrypt=encrypt)

        self.send_message(message)

    def on_message(self, message):
        message = proto.unpack_message(message)
        gevent.spawn(proto.process_message, self.send_message, message)

    def handshake(self):
        """returns None on error or (node_host, node_port)
        """
        data = {}
        data["name"] = base64.standard_b64encode(login.encrypt('frontend', socket.gethostname()))
        data["system"] = platform.system()
        data["machine"] = platform.machine()
        data["platform"] = platform.platform()
        data["release"] = platform.release()
        data["version"] = platform.version()

        result = AsyncResult()
        rid = str(id(result))
        self.login_results[rid] = result
        key = login.generate_backend_key()
        from .. import patch
        payload = {
            'id': rid,
            'version': proto.VERSION,
            'branch': patch.config.branch,
            'commit_id': patch.core_source.version,
            'l': login.get('login'),
            'system': data,
        }
        message_key = proto.pack_message('backend', 'api.set_key', payload=dict(key=key), encrypt="rsa")
        message = proto.pack_message('backend', 'api.login', payload=payload)

        try:
            self.send_message(message_key)
            self.send_message(message)
        except AttributeError:
            result.set([False, 'Client login error'])
            return
        try:
            result = result.get(timeout=20)
        except gevent.Timeout:
            result = ["False", "Login timed out"]
        finally:
            try:
                del self.login_results[rid]
            except KeyError:
                pass

        if not result[0]:
            log.error('login failed: {}'.format(result[1]))
            if result[1] == 'Invalid Login Credentials':
                self.connect_retry = 0
                login.logout()
            return False
        return True

    def connection_error(self):
        try:
            if not self.connect_error_dialog:
                self.connect_error_dialog = gevent.spawn(self._connection_error)
        finally:
            self.connect_retry = 0
            self.connect_error_dialog = None

    def _connection_error(self):
        elements = list()
        elements.append(input.Float('left'))
        elements.append(input.Text('Failed connecting to download.am.\nPlease check your internet connection.'))
        elements.append(input.Text(''))
        elements.append(input.Text('Proxy settings'))
        elements.append([input.Text('Type:'), input.Select('type', ('direct', 'http', 'https', 'socks4', 'socks5'), default=proxy.proxy_enabled() and proxy.config.type or 'direct')])
        elements.append([input.Text('Hostname:'), input.Input('host', value=proxy.config.host or '')])
        elements.append([input.Text('Port:'), input.Input('port', value=proxy.config.port or '')])

        elements.append(input.Text(''))
        elements.append(input.Text('Proxy authorization'))
        elements.append([input.Text('Username:'), input.Input('username', value=proxy.config.username or '')])
        elements.append([input.Text('Password:'), input.Input('password', 'password', value=proxy.config.password or '')])

        elements.append(input.Text(''))

        elements.append(input.Float('center'))
        elements.append(input.Choice('result', choices=[
            dict(value='save', content='Save', ok=True),
            dict(value='retry', content='Retry', cancel=True),
            dict(value='exit', content='Exit download.am')]))

        try:
            result = input.get(elements, type='api_connect', timeout=None, ignore_api=True)
        except input.InputAborted:
            return
        finally:
            self.connect_retry_last_msgbox = time.time()

        if result.get('result') == 'save':
            if result['type'] == 'direct':
                result['type'] = None
            result['enabled'] = True
            del result['result']
            interface.call('proxy', 'set', **result)
        elif result.get('result') == 'retry':
            return
        elif result.get('result') == 'exit':
            sys.exit(1)
        else:
            raise RuntimeError('invalid response: {}'.format(result))

    def run(self):
        def log_state(msg):
            if self.connection_state is not None:
                self.connection_state.put('connecting')
            log.debug(msg)

        error = False
        self.connect_retry = 0
        self.connect_error_dialog = None

        while True:
            self.close()
            self.connected_event.clear()

            login.wait()    # wait until user entered login data
            self.disconnected_event.clear()
            # TODO: wait for reconnected event

            if error:
                self.connect_retry += 1
                error = False
            else:
                self.connect_retry = 0

            sleep_time = (self.connect_retry + 1)*3
            if sleep_time > 15:
                sleep_time = 15

            log_state('connecting')
            try:
                with Timeout(30):
                    e = self.spawn(self.connect).get()
                    if e is not None:
                        raise e
                    self.emit("hello", settings.app_uuid, "client")
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as e:
                log_state('connection error')
                log.error('error connecting: {}'.format(e))
                self.close()
                event.fire('api:connection_error')
                error = True
                gevent.sleep(sleep_time)
                continue
            finally:
                self.kill()

            # close the connection error dialog
            self.connect_retry = 0
            if self.connect_error_dialog:
                self.connect_error_dialog.kill()
                self.connect_error_dialog = None

            # clear this event here to get acknowledged of new login data
            self.disconnected_event.clear()

            log_state('logging in')
            try:
                self.spawn(self.handshake)
                result = self.greenlet.get()
                if not result:
                    continue
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as e:
                log_state('login error')
                log.error('error handshaking: {}'.format(str(e)))
                self.close()
                error = True
                gevent.sleep(sleep_time)
                continue
            finally:
                self.kill()

            if self.disconnected_event.is_set():
                log.info('login data changed during handshake')
                continue

            if self.io is None or not self.io.connected:
                log.warning('api bootstrap done but not connected. this is strange...')
                log_state('connection error')
                self.close()
                error = True
                gevent.sleep(sleep_time)
                continue

            log_state('sending infos')
            payload = interface.call('api', 'expose_all')
            message = proto.pack_message('frontend', 'api.expose_all', payload=payload)
            try:
                self.send_message(message)
            except AttributeError:
                log.warning('api closed unexpected')
                log_state('connection error')
                self.close()
                error = True
                gevent.sleep(sleep_time)
                continue

            if self.disconnected_event.is_set():
                log.info('login data changed during handshake')
                continue

            self.connected_event.set()
            log_state('connected')

            event.fire('api:connected')
            self.disconnected_event.wait()
            login.hashes["backend"] = None
            event.fire('api:disconnected')

client = APIClient()

@event.register('login:changed')
@event.register('proxy:changed')
def _(e, *args):
    client.disconnected_event.set()

@event.register('reconnect:reconnecting')
def __(e, *args):
    client.disconnected_event.set()
    # TODO: wait for reconnected event
