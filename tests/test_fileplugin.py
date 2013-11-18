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

from . import loader
loader.init()

import os
import gevent
from gevent.pool import Pool

from client import event, core, fileplugin, debugtools, download, torrent
from client.scheme import transaction
from client import interface

fileplugin.init()

rootpath = os.path.dirname(__file__)


class FakeHosterPlugin(object):
    name = 'fake.com'
    download_pool = Pool(10)

    def weight(self, file):
        return 100

    def get_hostname(self, file):
        return 'fake.com'

    def get_download_context(self, account):
        pass


def _test_file(name, plugin):
    print '-'*100, "testing plugin {} with file {}".format(plugin, name)

    path = os.path.join(rootpath, name)
    with transaction:
        package = core.Package(name=os.path.splitext(name)[0], complete_dir=rootpath)
        file = core.File(package=package, name=name, url='file://{}'.format(path), host=FakeHosterPlugin(),
                         pmatch='asdf', state='download_complete')

    event.wait_for_events(['fileplugin:done'], 5)

    assert file.last_error is None
    assert file.working is False
    assert file.state == '{}_complete'.format(plugin)
    assert plugin in file.completed_plugins

    p = os.path.join(file.get_extract_path(), "1mb.bin")
    assert os.path.exists(p)

    try:
        debugtools.assert_file_checksum('md5', p, '934a5866d0a738c32f040559eccbf567')
    finally:
        os.unlink(p)

    file.delete()


def add_files(files, s1, s2, s3):
    with transaction:
        package = core.Package(name=os.path.splitext('1mb')[0], complete_dir=rootpath)
        name = '1mb.part1.rar'
        files.append(core.File(package=package, name=name,
                     url='file://{}'.format(os.path.join(rootpath, name)), host=FakeHosterPlugin(),
                     pmatch='asdf', state=s1))
        name = '1mb.part2.rar'
        files.append(core.File(package=package, name=name,
                     url='file://{}'.format(os.path.join(rootpath, name)), host=FakeHosterPlugin(),
                     pmatch='asdf', state=s2, working=True))
        name = '1mb.part3.rar'
        files.append(core.File(package=package, name=name, url='file://{}'.format(os.path.join(rootpath, name)),
                     host=FakeHosterPlugin(), pmatch='asdf', state=s3, working=True))


def test_rar_multipart():
    print "-"*100, 'test_rar_multipart'

    files = list()
    add_files(files, 'download_complete', 'download', 'download')

    event.wait_for_events(['rarextract:part_complete', 'rarextract:waiting_for_part'], 5)
    interface.call('core', 'printr')

    with transaction:
        if not "rarextract" in files[1].completed_plugins:
            print files[1].state
            files[1].state = 'download_complete'
            files[1].working = False

    event.wait_for_events(['rarextract:part_complete', 'rarextract:waiting_for_part'], 5)
    interface.call('core', 'printr')

    with transaction:
        if not "rarextract" in files[2].completed_plugins:
            print files[2].state
            files[2].state = 'download_complete'
            files[2].working = False

    event.wait_for_events(['rarextract:part_complete', 'rarextract:waiting_for_part'], 5)
    interface.call('core', 'printr')

    gevent.sleep(1)
    for f in files:
        f.join()
        assert f.last_error is None
        assert not f.working, "{} is working".format(f.name)
        assert f.state == 'rarextract_complete', "expected complete, but is {}".format(f.state)

    p = os.path.join(files[0].get_extract_path(), "1mb.bin")
    assert os.path.exists(p)

    try:
        debugtools.assert_file_checksum('md5', p, '934a5866d0a738c32f040559eccbf567')
    finally:
        os.unlink(p)

    for f in files:
        f.delete()


def _test_rar_multipart_start_stop():
    print '-'*100, 'test_rar_multipart_start_stop'

    files = list()
    add_files(files, 'download_complete', 'foo', 'foo')

    gevent.spawn_later(1.5, interface.call, 'core', 'stop')
    event.wait_for_events(['download:stopped'], 5)
    interface.call('core', 'printr')

    gevent.sleep(1)
    interface.call('core', 'start')
    event.wait_for_events(['rarextract:part_complete', 'rarextract:waiting_for_part'], 5)
    interface.call('core', 'printr')

    with transaction:
        files[1].state = 'download_complete'

    event.wait_for_events(['rarextract:part_complete', 'rarextract:waiting_for_part'], 5)
    interface.call('core', 'printr')

    with transaction:
        files[2].state = 'download_complete'

    event.wait_for_events(['rarextract:part_complete', 'rarextract:waiting_for_part'], 5)
    interface.call('core', 'printr')

    gevent.sleep(0.1)
    for f in files:
        f.join()
        assert f.last_error is None
        assert f.working is False
        assert f.state == 'rarextract_complete', "expected complete, but is {}".format(f.state)

    p = os.path.join(files[0].get_extract_path(), "1mb.bin")
    assert os.path.exists(p)

    try:
        debugtools.assert_file_checksum('md5', p, '934a5866d0a738c32f040559eccbf567')
    finally:
        os.unlink(p)

    for f in files:
        f.delete()


def test_fileplugin():
    _test_file('test_fileplugin.rar', 'rarextract')
    _test_file('test_fileplugin.zip', 'zipextract')
    test_rar_multipart()
    #test_rar_multipart_start_stop()

if __name__ == '__main__':
    test_fileplugin()
