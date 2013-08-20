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

import re
import sys
import socket as socketsocket

from gevent import socket
from gevent.lock import Semaphore
from netaddr import IPNetwork, IPAddress

from . import interface, event, logger
from .config import globalconfig
from .scheme import transaction
from .contrib import socks

log = logger.get('proxy')

config = globalconfig.new('proxy')
config.default('type', None, str)
config.default('host', None, unicode)
config.default('port', None, int)
config.default('username', None, str)
config.default('password', None, str)
config.default('enabled', None, bool)
config.default('last_error', None, unicode)

################################################# monkey patch socket

local_networks = (
    IPNetwork('127.0.0.0/8'),
    IPNetwork('10.0.0.0/8'),
    IPNetwork('172.16.0.0/12'),
    IPNetwork('192.168.0.0/16'),
    IPNetwork('240.0.0.0/8'),
    IPNetwork('255.0.0.0/8'))

def proxy_enabled():
    return config['type'] is not None and config['host'] is not None and config['port'] is not None and config['enabled'] and config['last_error'] is None

class wrapped_socket(socks.socksocket):
    def __init__(self, family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0, _sock=None, force_direct_connect=False):
        self._force_direct_connect = force_direct_connect
        socks.socksocket.__init__(self, family, type, proto, _sock)

    def connect(self, destpair):
        for res in socket.getaddrinfo(destpair[0], destpair[1], self.family, self.type, self.proto):
            af, socktype, proto, _canonname, sa = res
            ip = IPAddress(sa[0])
            if self._force_direct_connect or config['type'] is None or not config['enabled'] or config['last_error'] or any(ip in network for network in local_networks):
                self.setproxy()
            else:
                self.setproxy(proxytype=type_to_int(config['type']), addr=config['host'], port=config['port'], rdns=True, username=config['username'], password=config['password'])
            socks.socksocket.connect(self, sa)
            break

# stolen from gevent/socket.py
def create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
    """Connect to *address* and return the socket object.

    Convenience function. Connect to *address* (a 2-tuple ``(host,
    port)``) and return the socket object. Passing the optional
    *timeout* parameter will set the timeout on the socket instance
    before attempting to connect. If no *timeout* is supplied, the
    global default timeout setting returned by :func:`getdefaulttimeout`
    is used. If *source_address* is set it must be a tuple of (host, port)
    for the socket to bind as a source address before making the connection.
    An host of '' or port 0 tells the OS to use the default.
    """

    host, port = address
    err = None

    force_direct_connect = host == globalconfig['reconnect.routerip'] and True or False

    for res in socket.getaddrinfo(host, port, 0 if socket.has_ipv6 else socket.AF_INET, socket.SOCK_STREAM):
        af, socktype, proto, _canonname, sa = res
        sock = None
        try:
            sock = socket.socket(af, socktype, proto, force_direct_connect=force_direct_connect)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sa)
            return sock
        except socket.error:
            err = sys.exc_info()[1]
            # without exc_clear(), if connect() fails once, the socket is referenced by the frame in exc_info
            # and the next bind() fails (see test__socket.TestCreateConnection)
            # that does not happen with regular sockets though, because _socket.socket.connect() is a built-in.
            # this is similar to "getnameinfo loses a reference" failure in test_socket.py
            sys.exc_clear()
            if sock is not None:
                sock.close()
    if err is not None:
        raise err
    else:
        raise socket.error("getaddrinfo returns an empty list")

socket.socket = wrapped_socket
socket.create_connection = create_connection

socketsocket.socket = wrapped_socket
socketsocket.create_connection = create_connection


################################################# plugin code

def type_to_int(type):
    if type == 'http':
        return socks.PROXY_TYPE_HTTP
    elif type == 'https':
        return socks.PROXY_TYPE_HTTPS
    elif type == 'socks4':
        return socks.PROXY_TYPE_SOCKS4
    elif type == 'socks5':
        return socks.PROXY_TYPE_SOCKS5
    else:
        raise ValueError('invalid proxy type: {}'.format(type))

test_page = (('patch.download.am', 80), '/ip')

def check_ip(ip):
    if not re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
        raise ValueError('invalid ip: {}'.format(ip))

def check_proxy(type, host, port, username, password):
    # wait until we have an internet connection
    log.info('checking proxy {}://{}:{}'.format(type, host, port))

    #resp = requests.get('http://{}:{}{}'.format(test_page[0][0], test_page[0][1], test_page[1]))
    #old_ip = resp.content.strip()
    #check_ip(old_ip)

    s = socks.socksocket()
    s.setproxy(proxytype=type_to_int(type), addr=host, port=int(port), rdns=True, username=username, password=password)
    s.connect(test_page[0])

    request = []
    request.append('GET {} HTTP/1.0'.format(test_page[1]))
    request.append('Host: {}'.format(test_page[0][0]))
    request.append('Connection: close')
    request.append('')
    request.append('')
    s.send('\r\n'.join(request))

    ip = s.recv(8192).split('\r\n\r\n', 1)[1].strip()
    check_ip(ip)

    log.info('proxy check successful')

@interface.register
class Interface(interface.Interface):
    name = 'proxy'

    def set(type=None, host=None, port=None, username=None, password=None, enabled=True):
        old_enabled = config['enabled']
        error = None
        ip_changed = False
        if type and host and port and enabled:
            type = type.lower()
            try:
                ip_changed = check_proxy(type, host, port, username, password)
            except BaseException as e:
                log.error('proxy check failed: {}'.format(e))
                error = e
        else:
            enabled = False
        with transaction:
            config['type'] = type or None
            config['host'] = host or None
            config['port'] = port or None
            config['username'] = username or None
            config['password'] = password or None
            config['enabled'] = enabled and not error and True or False
            config['last_error'] = error and str(error) or None
        if old_enabled != config['enabled']:
            event.fire('proxy:changed')
            if ip_changed:
                event.fire('ip:changed')
        return dict(enabled=config['enabled'])

    def remove():
        with transaction:
            config['type'] = None
            config['host'] = None
            config['port'] = None
            config['username'] = None
            config['password'] = None
            config['enabled'] = False
            config['checking'] = False
            config['last_error'] = None
        event.fire('proxy:changed')

    def enable(enable=True):
        if enable:
            if not config.type or not config.host or not config.port:
                return False
            if not config['enabled']:
                config['enabled'] = True
                ip_changed = check_proxy(config.type, config.host, config.port, config.username, config.password)
                event.fire('proxy:changed')
                if ip_changed:
                    event.fire('ip:changed')
        else:
            if config['enabled']:
                config['enabled'] = False
                event.fire('proxy:changed')
                event.fire('ip:changed')
        return config['enabled']


def init():
    pass

lock = Semaphore()

#@event.register('api:connection_error')
def _(e):
    if not config['enabled'] or config['last_error']:
        return
    with lock:
        try:
            check_proxy(config['type'], config['host'], config['port'], config['username'], config['password'])
        except BaseException as e:
            config['enabled'] = False
            config['last_error'] = str(e)
            log.warning('disabling proxy. it seems to be dead: {}'.format(e))
            event.fire('proxy:changed')
