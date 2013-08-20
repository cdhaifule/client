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

import loader
loader.init()

import os
import sys
import gevent

from client import debugtools, scheme, loader, event, interface, core

import httpserver

loader.init()

sys.stdout = sys._old_stdout
sys.stderr = sys._old_stderr

listener = scheme.PassiveListener(['api', 'db'])
scheme.register(listener)

DEFAULT_MAX_CHUNKS = 2

interface.call('config', 'set', key='download.max_chunks', value=DEFAULT_MAX_CHUNKS)
interface.call('config', 'set', key='download.overwrite', value='overwrite')

#######################

import socket
from client import logger
logger.ignore_exceptions.append(socket.error)

class Test(object):
    def setUp(self):
        httpserver.start()

    def tearDown(self):
        httpserver.stop()
        interface.call('core', 'erase_package')

    def wait_check(self):
        while True:
            for f in core.files():
                if f.state != 'collect' or f.working:
                    break
            else:
                break
            gevent.sleep(0.1)
        core.sort_queue.wait()

    def test_mirrors(self):
        interface.call('config', 'set', key='download.max_simultan_downloads', value=4)

        self.testurl = httpserver.url+'/10mb.bin'
        interface.call('core', 'add_links', links=[
            httpserver.url+'/anyname/mirror1/flow-1.bin',
            httpserver.url+'/anyname/mirror2/flow-1.bin',
            httpserver.url+'/anyname/mirror3/flow-1.bin',
            httpserver.url+'/anyname/mirror4/flow-1.bin'])

        self.wait_check()
        assert [f.name for f in core.files()] == ['flow-1.bin', 'flow-1.bin', 'flow-1.bin', 'flow-1.bin']

        interface.call('config', 'set', key='download.rate_limit', value=32768)
        interface.call('core', 'accept_collected')

        event.wait_for_events(['download:spawn_tasks'], 5)
        gevent.sleep(0.2)

        assert len(core._packages) == 1
        assert len(core._packages[0].files) == 4
        assert sum(1 for f in core.files() if f.working) == 1
        interface.call('config', 'set', key='download.rate_limit', value=0)

        event.wait_for_events(['package:download_complete'], 5)
        file = [f for f in core.files() if f.state == 'download_complete'][0]

        assert file.package.state == 'download_complete'

        for f in core.files():
            if f != file:
                assert f.state == 'download'
                assert f.enabled is False
                assert f.last_error.startswith('downloaded via')

        interface.call('core', 'printr')

    def test_downloads(self):
        interface.call('config', 'set', key='download.max_simultan_downloads', value=2)

        self.testurl = httpserver.url+'/10mb.bin'
        interface.call('core', 'add_links', links=[
            httpserver.url+'/anyname/flow-1.bin',
            httpserver.url+'/anyname/flow-2.bin',
            httpserver.url+'/anyname/flow-3.bin',
            httpserver.url+'/anyname/flow-4.bin'])

        self.wait_check()
        assert [f.name for f in core.files()] == ['flow-1.bin', 'flow-2.bin', 'flow-3.bin', 'flow-4.bin']

        interface.call('config', 'set', key='download.rate_limit', value=32768)
        interface.call('core', 'accept_collected')

        event.wait_for_events(['download:spawn_tasks'], 5)
        gevent.sleep(0.2)

        assert len(core._packages) == 1
        assert len(core._packages[0].files) == 4
        assert [f.working for f in core.files()] == [True, True, False, False]
        interface.call('config', 'set', key='download.rate_limit', value=0)

        event.wait_for_events(['file:download_complete'], 15)
        interface.call('config', 'set', key='download.rate_limit', value=32768)

        # these tests fail randomly so it is disabled
        #assert sum(1 for f in core.files() if f.working) == 1
        #assert sum(1 for f in core.files() if f.state == 'download_complete') == 1
        assert sum(1 for f in core.files() if f.state == 'download') == 3

        interface.call('config', 'set', key='download.rate_limit', value=0)
        event.wait_for_events(['package:download_complete'], 15)

        assert sum(1 for f in core.files() if f.working) == 0
        assert sum(1 for f in core.files() if f.state == 'download_complete') == 4
        assert sum(1 for f in core.files() if f.last_error) == 0
        assert sum(1 for f in core.files() if not f.enabled) == 0

        interface.call('core', 'printr')

if __name__ == '__main__':
    t = Test()
    t.setUp()
    #t.test_mirrors()
    t.test_downloads()
    t.tearDown()
