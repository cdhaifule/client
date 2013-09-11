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
import json
import socket
import chardet
import traceback

from gevent import Timeout
from gevent.server import StreamServer

from . import interface, logger, event
from .localize import _T

log = logger.get('localrpc')

RPC_ADDR = '127.0.0.1', 13035

try: # from cherrypy wsgi server
    import fcntl
except ImportError:
    try:
        from ctypes import windll, WinError
    except ImportError:
        def prevent_socket_inheritance(sock):
            """Dummy function, since neither fcntl nor ctypes are available."""
            pass
    else:
        def prevent_socket_inheritance(sock):
            """Mark the given socket fd as non-inheritable (Windows)."""
            if not windll.kernel32.SetHandleInformation(sock.fileno(), 1, 0):
                raise WinError()
else:
    def prevent_socket_inheritance(sock):
        """Mark the given socket fd as non-inheritable (POSIX)."""
        fd = sock.fileno()
        old_flags = fcntl.fcntl(fd, fcntl.F_GETFD)
        fcntl.fcntl(fd, fcntl.F_SETFD, old_flags | fcntl.FD_CLOEXEC)

def send(s, data):
    s.sendall("%10s" % len(data))
    s.sendall(data)

def recv(s):
    data = s.recv(10)
    if not data:
        return
    l = int(data.strip())
    data = ''
    while l > 0:
        d = s.recv(l)
        l -= len(d)
        data += d
    return data

def listen_accept(conn, addr):
    log.info('accepted connection from {}:{}'.format(*addr))
    try:
        data = conn.recv(8192)
        if data != 'ping':
            raise ValueError('handshake failed')
        conn.sendall('pong')
        while True:
            data = recv(conn)
            if not data:
                break
            try:
                data = data.decode(chardet.detect(data)['encoding'])
            except TypeError:
                pass
            command, module, kwargs = json.loads(data)
            data = interface.call(command, module, **kwargs)
            data = json.dumps(data)
            send(conn, data)
    except BaseException:
        log.error('connection to {}:{} closed: {}'.format(addr[0], addr[1], traceback.format_exc()))
    else:
        log.info('connection to {}:{} closed'.format(*addr))
    finally:
        conn.close()


pending = list()
exit_after_exec = False

def client_connect():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect(RPC_ADDR)
        s.sendall('ping')
        data = s.recv(8192)
        if data != 'pong':
            raise ValueError('handshake failed')
    except socket.error as msg:
        if msg.errno == 'foooo':
            log.error('error connecting to local rpc. is the client running?')
            sys.exit()
        raise
    return s

def client_send_pending(s):
    log.info('connected to {}:{}'.format(*RPC_ADDR))
    try:
        for rpc in pending:
            log.debug('sending {}'.format(rpc))
            send(s, json.dumps(rpc))
            result = recv(s)
            log.debug('received {}'.format(result))
    finally:
        s.close()
    log.info('all commands executed. exit')
    sys.exit()

@event.register('loader:initialized')
def execute_pending(e):
    global pending
    for rpc in pending or list():
        log.debug('executing {}'.format(rpc))
        result = interface.call(rpc[0], rpc[1], **rpc[2])
        log.debug('result {}'.format(result))
    pending = None
    if exit_after_exec:
        log.info('all commands executed. exit')
        sys.exit()

listener = None

def init_optparser(parser, OptionGroup):
    parser.usage += _T.rpc__usage
    parser.epilog = _T.rpc__epilog
    group = OptionGroup(parser, _T.rpc__options)
    group.add_option('--exit-after-exec', dest="exit_after_exec", action="store_true", default=False, help=_T.rpc__exit_after_exec)
    parser.add_option_group(group)

def already_running(s):
    global pending
    if pending:
        client_send_pending(s)
    else:
        pending = [['browser', 'open_browser', {}]]
        client_send_pending(s)
        log.error('local rpc listen address already taken. application already running?')
    sys.exit()

def init(options, args):
    global pending
    global listener
    global exit_after_exec
    
    exit_after_exec = options.exit_after_exec

    # parse command line arguments
    for rpc in args:
        try:
            try:
                rpc = rpc.decode(chardet.detect(rpc)['encoding'])
            except TypeError:
                pass
            try:
                command, args = rpc.split(' ', 1)
            except ValueError:
                command, args = rpc, ''
            try:
                module, command = command.rsplit('.', 1)
            except ValueError:
                log.error(u"could not work with command {}".format(rpc))
                continue
            kwargs = dict()
            for arg in filter(lambda a: a and True or False, args.split(' -- ')):
                key, value = arg.split('=', 1)
                try:
                    value = json.loads(value)
                except ValueError:
                    pass
                kwargs[key.strip()] = value
        except ValueError:
            log.critical(u'rpc command line error: {}'.format(rpc))
            raise
            sys.exit()
        pending.append([module, command, kwargs])

    # win32 allows more than one process to listen on one ip:port,
    # so we try to connect to check if there is already another instance running
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with Timeout(5): # needed cause this action can hang on win32 for a longer time
            s = client_connect()
    except ValueError:
        already_running(s)
    except:
        pass
    else:
        already_running(s)
    finally:
        s.close()
    
    try:
        listener = StreamServer(RPC_ADDR, listen_accept)
        listener.start()
        prevent_socket_inheritance(listener.socket)
    except socket.error as msg:
        # second check if client is already running
        if msg.errno in (48, 98, 10048):
            s = client_connect()
            try:
                already_running(s)
            finally:
                s.close()
        raise

def terminate():
    if listener:
        listener.stop()
