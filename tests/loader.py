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

from __future__ import absolute_import

from client import monkey
monkey.init()

from gevent.event import Event

from client import interface
interface.ignore_protected_functions = True

from client import settings, db, api, ui, service, loader, login, logger, localrpc, config, interface, patch

api.is_connected = lambda: True
api.init = lambda: None
ui.init = lambda: None
service.init = lambda: None
localrpc.init = lambda: None


original_loader_init = loader.init
loader_initialized = False
def loader_init():
    global loader_initialized
    if not loader_initialized:
        original_loader_init()
        loader_initialized = True
loader.init = loader_init

def loader_init_optparser():
    class FakeOptions(object):
        def __getattr__(self, key):
            return None
    return FakeOptions(), list()
loader._init_optparser = loader_init_optparser

patch.init = lambda: None

def init():
    logger.log_console_level = logger.logging.DEBUG

    login.module_initialized = Event()

    settings.db_file = ':memory:'
    settings.next_uid_file = 0
    settings.config_file = None
    settings.log_file = None

    settings.init()
    db.init()
    config.init()

    interface.call('config', 'set', key='check.use_cache', value=False)
    
    login.set_login('rico@download.net', 'helloworld')
