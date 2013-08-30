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

import gevent

from gevent.queue import JoinableQueue

from .. import scheme, interface, settings
from . import proto, server
from .client import client
from .push import listener
from ..localize import _T

@interface.register
class Interface(interface.Interface):
    name = 'api'

    def expose_all():
        update = []
        for table in scheme.scheme.all_tables.values():
            data = table.serialize(listener.channels)
            if data:
                data['table'] = table._table_name
                data['id'] = table.id
                data['action'] = 'new'  # send data as "new"
                update.append(data)
        if update:
            return listener.prepare(update)
            
    def logout():
        reconnect()
        return True

    def login(id=None, success=None, listen=None, err=None, c=None, ip=None, guest=None, authenticator=None):
        if id not in client.login_results:
            proto.log.critical('login request {} not found'.format(id))
            #return 'login request not found'
            return
        from .. import login
        try:
            assert guest == login.is_guest()
        except:
            proto.log.unhandled_exception('Remote guest state: {}, local guest state {}'.format(guest, login.is_guest()))
        if not success or err:
            client.login_results[id].set([False, err, None])
        else:
            client.login_results[id].set([True, listen, c])

    def ping():
        return {'uuid': settings.app_uuid}

is_connected = client.is_connected
wait_connected = client.wait_connected

send = proto.send

_greenlet = None

def reconnect():
    client.disconnected_event.set()

def init_optparser(parser, OptionGroup):
    group = OptionGroup(parser, _T.api__options)
    group.add_option('--api-log', dest="api_log", action="store_true", default=False, help=_T.api_log)
    group.add_option('--test-backend', dest="testbackend", default=None, help="internal")
    group.add_option('--test-no-change-node', dest="nochangenode", action="store_false", default=True, help="internal")
    parser.add_option_group(group)

def init(options):
    global _greenlet

    proto.debug = options.api_log
    scheme.register(listener)
    
    if options.testbackend:
        client.change_node = options.nochangenode
        client.node = (options.testbackend, 443)

    # start the main api loop
    proto.client = client
    client.connection_state = JoinableQueue()
    _greenlet = gevent.spawn(client.run)
    while not is_connected():
        result = client.connection_state.get()
        yield result
        if result == 'connected' or is_connected():
            break
    client.connection_state = None

    # start the direct connect server
    server.init()

def terminate():
    server.terminate()
    
    if _greenlet:
        try:
            _greenlet.kill()
        except AssertionError:
            pass
        client.close()
