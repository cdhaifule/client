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

import sys
import gevent

from client import debugtools, scheme, loader, event, interface

import httpserver

loader.init()

sys.stdout = sys._old_stdout
sys.stderr = sys._old_stderr

listener = scheme.PassiveListener(['api', 'db'])
scheme.register(listener)

DEFAULT_MAX_CHUNKS = 2
SLEEP = (2.5, 1.2)

interface.call('config', 'set', key='download.max_chunks', value=DEFAULT_MAX_CHUNKS)
interface.call('config', 'set', key='download.overwrite', value='overwrite')

#######################

import socket
from client import logger
logger.ignore_exceptions.append(socket.error)

class Test(object):
    testurl = httpserver.url+'/10mb.bin'

    def setUp(self):
        httpserver.start()

    def tearDown(self):
        httpserver.stop()

    def add_link(self):
        id = debugtools.add_links(self.testurl, auto_accept=True)[0]
        self.file = scheme.get_by_uuid(id)

    def del_link(self):
        self.file.erase()

    def file_enabled(self, enabled):
        if self.file.enabled != enabled:
            with scheme.transaction:
                self.file.enabled = enabled
            gevent.sleep(0.1)

    def assert_download_complete(self):
        assert debugtools.compare_dict(self.file.serialize(), {'substate': [None], 'last_error': None, 'hash_type': None, 'chunks': 0, 'hash_value': None, 'size': 10485760, 'state': 'download_complete', 'name': '10mb.bin', 'enabled': True, 'working': False})

    def assert_download_incomplete(self, num_chunks, chunk_pos):
        assert debugtools.compare_dict(self.file.serialize(), {'working': False, 'substate': [None], 'name': '10mb.bin', 'last_error': None, 'hash_type': None, 'state': 'download', 'approx_size': None, 'hash_value': None, 'size': 10485760})
        assert len(self.file.chunks) == num_chunks
        for chunk in self.file.chunks:
            assert debugtools.compare_dict(chunk.serialize(), {'state': 'download', 'file': self.file.id})
            if chunk_pos == '>':
                assert chunk.pos > chunk.begin
            elif chunk_pos == '==':
                assert chunk.pos == chunk.begin
            elif chunk_pos == '>=':
                assert chunk.pos >= chunk.begin
        assert self.file.chunks[-1].end == self.file.size

    def finish_download(self, type, speed=0):
        print "!!!", '{} #finish'.format(type)

        interface.call('config', 'set', key='download.rate_limit', value=speed)
        self.file_enabled(True)
        event.wait_for_events(['file:download_complete'], 10)
        self.assert_download_complete()
        debugtools.assert_file_checksum('md5', self.file.get_complete_file(), httpserver.md5_10mb)
        self.del_link()

    def _default_flow_test(self, type, num_chunks, chunk_pos='>', callback=None):
        interface.call('config', 'set', key='download.rate_limit', value=100*1024)

        print "!!!", '{} #1'.format(type)
        self.add_link()
        gevent.sleep(SLEEP[0])
        self.file_enabled(False)
        self.assert_download_incomplete(num_chunks, chunk_pos)
        cache = [chunk.serialize() for chunk in self.file.chunks]

        if callback:
            callback()

        print "!!!", '{} #2'.format(type)
        self.file_enabled(True)
        gevent.sleep(SLEEP[1])
        self.file_enabled(False)
        self.assert_download_incomplete(num_chunks, chunk_pos)

        return cache

    def test_complete(self):
        self.testurl = httpserver.url+'/10mb.bin'
        self.add_link()
        self.finish_download('complete')

    def test_resume(self):
        self.testurl = httpserver.url+'/resume/10mb.bin'
        cache = self._default_flow_test('resume', 2)

        for i in range(len(self.file.chunks)):
            assert cache[i]['id'] == self.file.chunks[i].id
            assert cache[i]['pos'] < self.file.chunks[i].pos

        self.finish_download('resume')

    def _test_no_resume(self, type):
        cache = self._default_flow_test(type, 1)

        for i in range(len(self.file.chunks)):
            assert cache[i]['id'] != self.file.chunks[i].id
            assert cache[i]['pos'] >= self.file.chunks[i].pos

        self.finish_download(type)

    def test_no_resume(self):
        self.testurl = httpserver.url+'/noresume/10mb.bin'
        self._test_no_resume('no resume')

    def _connection_limit_callback(self):
        def callback():
            gt, eq = 0, 0
            for chunk in self.file.chunks:
                if chunk.pos == chunk.begin:
                    eq += 1
                elif chunk.pos > chunk.begin:
                    gt += 1
            assert eq == 3
            assert gt == 2

    def test_connection_limit(self):
        interface.call('config', 'set', key='download.max_chunks', value=6)
        self.testurl = httpserver.url+'/resume/connection_limit/10mb.bin'

        cache = self._default_flow_test('connection limit', 6, '>=', self._connection_limit_callback)

        self._connection_limit_callback()
        for i in range(len(self.file.chunks)):
            chunk = self.file.chunks[i]
            assert cache[i]['id'] == chunk.id
            if chunk.pos >= chunk.begin:
                assert cache[i]['pos'] <= chunk.pos

        self.finish_download('connection limit', 5*1024**2)
        interface.call('config', 'set', key='download.max_chunks', value=DEFAULT_MAX_CHUNKS)

    def test_connection_limit_no_resume(self):
        self.testurl = httpserver.url+'/noresume/connection_limit/10mb.bin'
        interface.call('config', 'set', key='download.max_chunks', value=5)
        self._test_no_resume('connection limit + no resume')
        interface.call('config', 'set', key='download.max_chunks', value=DEFAULT_MAX_CHUNKS)

    def test_start_stop(self):
        interface.call('config', 'set', key='download.max_chunks', value=3)
        interface.call('config', 'set', key='download.rate_limit', value=30*1024)

        self.testurl = httpserver.url+'/resume/10mb.bin'
        self.add_link()
        gevent.sleep(SLEEP[0])

        assert len(self.file.chunks) == 3
        assert self.file.chunks_working == 3

        interface.call('core', 'stop')

        gevent.sleep(SLEEP[0])

        self.assert_download_incomplete(3, '>')
        assert self.file.chunks_working == 0

        interface.call('core', 'start')

        self.finish_download('resume')
        interface.call('config', 'set', key='download.max_chunks', value=DEFAULT_MAX_CHUNKS)

if __name__ == '__main__':
    t = Test()
    t.setUp()
    t.test_complete()
    t.test_resume()
    t.test_no_resume()
    t.test_connection_limit()
    #t.test_connection_limit_no_resume()
    t.test_start_stop()
    t.tearDown()
