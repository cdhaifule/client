# -*- coding: utf-8 -*-
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
import gevent

from tests import httpserver

from . import debugtools, interface, patch, api, settings, localrpc, scheme, event, login, logger

def init():
    # set a dummy api account
    if True:
        login.set_login('dummy', 'account', type='account')

    # allow the use of protected interface functions
    if True:
        interface.ignore_protected_functions = True

    # set patch system to test mode
    if True:
        patch.test_mode = True

    # disable api connection
    if True:
        api.init = lambda: None
        api.is_connected = lambda: True

    # set database to memory
    if True:
        settings.db_file = ':memory:'

    # diable localrpc
    if True:
        localrpc.init = lambda: None

    # disable close of stdout/stderr
    if True:
        logger.test_mode = True

    # register debug scheme listener
    if False:
        scheme.register(scheme.DebugListener(('api',), 0))

    # disable splash screen
    if True:
        if '--disable-splash' not in sys.argv:
            sys.argv.append('--disable-splash')

    # start our test function when loader is initialized
    if True:
        event.add('loader:initialized', lambda e: gevent.spawn(main))

def main():
    """insert your test code here. this function is called right after the bootstrap
    you see some example calls below
    """
    print >>sys.stderr, u'PLEASE COPY "{}" TO "{}" AND PLACE YOUR TESTCODE INSIDE'.format(__file__, __file__.replace('testdefault.py', 'test.py'))
    sys.exit(1)

    # start the download engine
    interface.call('core', 'start')

    # disable remote check cache
    interface.call('config', 'set', key="check.use_cache", value=False)

    # add a hoster account
    interface.call('account', 'add', name="example.com", username="user", password="verysecure")

    # add a download link and leave it in linkcollector after check
    debugtools.add_links('http://example.com/file/1mb.bin')

    # add a download link and start download directly after successful check
    debugtools.add_links('http://example.com/file/2mb.bin')

    # change some config variables
    interface.call('config', 'set', key="download.max_simultan_downloads", value=1)
    interface.call('config', 'set', key="download.max_chunks", value=1)

    # start our test http server
    httpserver.start()

    # add a download from our http test server with known md5 value
    debugtools.add_links([dict(url='http://localhost:4567/anyname/mirror1/1mb.bin', hash_type='md5', hash_value='9a978dd12f07769d9b4cc77b1d38de9c')], auto_accept=True)

    # sleep for a moment
    gevent.sleep(2)

    # call core.stop
    interface.call('core', 'stop')

    # stop test http server
    httpserver.stop()

    # loop forever and print a very dummy download list
    while True:
        interface.call('core', 'printr')
        gevent.sleep(1)
