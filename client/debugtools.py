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

import os
import yaml
import gevent
import hashlib

from . import interface, event, logger

tests = dict()
auto_accept_ids = []

log = logger.get('debugtools') # test patch

def load_test(path):
    log.info('loading test file {}'.format(path))
    with open(path, 'r') as f:
        for host, value in yaml.load(f).iteritems():
            if not host in tests:
                tests[host] = value
            else:
                for test, value in value.iteritems():
                    if not test in tests[host]:
                        tests[host][test] = value
                    else:
                        log.warning('test {} {} already exists!'.format(host, test))

def load_tests():
    load_test(os.path.join(os.path.split(__file__)[0], '..', 'test_hosters.yaml'))
    path = os.path.join(os.path.split(__file__)[0], '..', 'setup', 'test_hosters.yaml')
    if os.path.exists(path):
        load_test(path)

def add_links(links, auto_accept=False):
    ids = interface.call('core', 'add_links', links=links)
    if auto_accept:
        for id in ids:
            auto_accept_ids.append(id)
    return ids

def start_test(name, test, auto_accept=False):
    if not tests:
        load_tests()
    test = tests[name][test]
    if 'account' in test:
        try:
            interface.call('account', 'add', name=name, **test['account'])
        except ValueError:
            pass
    links = []
    if 'url' in test:
        links.append(test['url'])
    if 'urls' in test:
        if isinstance(test['urls'], basestring):
            test['urls'] = [test['urls']]
        links += test['urls']
    if links:
        return add_links(links, auto_accept=auto_accept)

@event.register('file:checked')
#@event.register('file:greenlet_stop')
def on_file_checked(e, file):
    ids = []
    if file.id in auto_accept_ids:
        for file in file.package.files:
            if file.state != 'collect' or file.working:
                return
            ids.append(file.id)
        gevent.spawn_later(0.1, interface.call, 'core', 'accept_collected', id=file.package.id)
        for id in ids:
            if id in auto_accept_ids:
                auto_accept_ids.remove(id)

@event.register('file:created')
def on_file_created(e, file):
    if file.id in auto_accept_ids:
        if file.state == 'check' and file.last_error == 'link already exists':
            file.delete()

def compare_dict(a, b):
    for k, v in b.iteritems():
        if not k in a or a[k] != v:
            raise AssertionError('key {}: "{}" != "{}"'.format(k, a[k], b[k]))
    return True

def assert_file_checksum(algo, file, checksum):
    h = getattr(hashlib, algo)()
    with open(file, 'rb') as f:
        h.update(f.read())
    assert h.hexdigest() == checksum

def test_search(plugin, query, max_results=50):
    r = interface.call('hoster', 'search', id=1234, search_id=5678, plugins=[plugin], query=query, max_results=max_results)
    yield r
    while r['more']:
        r = interface.call('hoster', 'search_more', id=1234, search_id=5678)
        yield r
