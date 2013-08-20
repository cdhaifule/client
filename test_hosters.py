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
import gevent
import subprocess

from gevent.pool import Pool
from gevent.lock import Semaphore

from tests import loader
loader.init()

from client import debugtools, hoster, interface, event, scheme, logger
from client.contrib import sizetools

log = logger.get('test_hosters')

######################### startup code

debugtools.load_tests()

if len(sys.argv) < 3:
    success = True
    errors = []
    lock = Semaphore()

    def start_plugin(plugin):
        global success

        cmd = 'python -m test_hosters {} 2>&1'.format(plugin)
        proc = subprocess.Popen([cmd], shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        retval = proc.wait()
        if retval != 0:
            data = proc.communicate()[0]
            with lock:
                print data
                print >>sys.stderr, 'test_hosters', '...', 'FAILED', '...', plugin
            success = False
        else:
            print >>sys.stderr, 'test_hosters', '...', 'success', '...', plugin

    def start_test(plugin, test):
        global success

        cmd = 'python -m test_hosters {} {} 2>&1'.format(plugin, test)
        proc = subprocess.Popen([cmd], shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        retval = proc.wait()
        if retval != 0:
            data = proc.communicate()[0]
            data = data.splitlines()
            prefix = "\n{} {}: ".format(plugin, test)
            msg = "{}{}".format(prefix, prefix.join(data)).strip()
            with lock:
                print >>sys.stderr, msg
                print >>sys.stderr, 'test_hosters', '...', 'FAILED', '...', plugin, test
            success = False
        else:
            print >>sys.stderr, 'test_hosters', '...', 'success', '...', plugin, test

    if len(sys.argv) == 1:
        pool = Pool(size=50)
        plugins = interface.call('hoster', 'list_plugins')
        for plugin in debugtools.tests.keys():
            if not plugin in debugtools.tests:
                print >>sys.stderr, 'test_hosters', '...', 'MISSING', '...', plugin
                continue
            with lock:
                print >>sys.stderr, 'test_hosters', '...', 'starting', '...', plugin
                pool.spawn(start_plugin, plugin)
        pool.join()

    elif len(sys.argv) == 2:
        plugin = debugtools.tests[sys.argv[1]]
        for test in plugin:
            with lock:
                print >>sys.stderr, 'test_hosters', '...', 'starting', '...', sys.argv[1], test
            start_test(sys.argv[1], test)

    if success:
        exit(0)
    else:
        exit(1)

######## get our test case

plugin = sys.argv[1]
testcase = sys.argv[2]
test = debugtools.tests[plugin][testcase]

def compile_test(d):
    for k in d.keys():
        if type(d[k]) == dict:
            compile_test(d[k])
        elif k == 'size' and isinstance(d[k], basestring):
            d[k] = sizetools.human2bytes(d[k])
        elif k == 'urls' and isinstance(d[k], basestring):
            d[k] = [d[k]]
        elif k == 'url':
            d['urls'] = [d[k]]
            del d[k]

compile_test(test)

default_test = dict()
default_test['type'] = 'hoster'
default_test['timeout'] = {'account': 60, 'check': 60, 'download': 150, 'wait': 110, 'retry': 110}

for k, v in default_test.iteritems():
    if type(v) == dict:
        v.update(k in test and test[k] or dict())
        test[k] = v
    elif not k in test:
        test[k] = v

######## load modules

from client import input
from client import db, account, core, check, download

objects = [db, hoster, account, core, check, download]
for obj in objects:
    obj.init()

######## monkey patch input class

def captcha(data, mime, parent=None, **kwargs):
    parent.fatal('INPUT IGNORED')

def password(parent=None, **kwargs):
    parent.fatal('INPUT IGNORED')

input.captcha = captcha
input.captcha_text = captcha
input.captcha_image = captcha
input.password = password

######## monkey patch core functions wait and retry

original_file_wait = core.File.wait
def file_wait(self, seconds):
    if seconds > test['timeout']['wait']:
        log.error('file wait time of {} too long'.format(seconds))
        self.fatal('WAIT TOO LONG')
    return original_file_wait(self, seconds)
core.File.wait = file_wait

original_chunk_wait = core.Chunk.wait
def chunk_wait(self, seconds):
    if seconds > test['timeout']['wait']:
        log.error('chunk wait time of {} too long'.format(seconds))
        self.fatal('WAIT TOO LONG')
    return original_chunk_wait(self, seconds)
core.Chunk.wait = chunk_wait

original_file_retry = core.File.retry
def file_retry(self, msg, seconds, need_reconnect=False):
    if seconds > test['timeout']['retry']:
        log.error('file retry time of {} too long: {}'.format(seconds, msg))
        self.fatal('RETRY TOO LONG')
    return original_file_retry(self, msg, seconds, need_reconnect=need_reconnect)
core.File.retry = file_retry

original_chunk_retry = core.Chunk.retry
def chunk_retry(self, msg, seconds, need_reconnect=False):
    if seconds > test['timeout']['retry']:
        log.error('chunk retry time of {} too long: {}'.format(seconds, msg))
        self.fatal('RETRY TOO LONG')
    return original_chunk_retry(self, msg, seconds, need_reconnect=need_reconnect)
core.Chunk.retry = chunk_retry

######## monkey patch captcha modules

from client.captcha import adscaptcha, recaptcha, solvemedia

def fake_solve(browser, challenge_id, block=True, timeout=30, parent=None):
    parent.fatal('INPUT IGNORED')

adscaptcha.solve = fake_solve
recaptcha.solve = fake_solve
solvemedia.solve = fake_solve

######## tool functions

def check_values(test, step, obj):
    if not step in test:
        return
    test = test[step]
    if type(test) == list:
        last_exc = None
        for t in test:
            try:
                _check_values(t, step, obj)
                return
            except ValueError as e:
                last_exc = e
        if last_exc:
            raise last_exc
    else:
        _check_values(test, step, obj)

def _check_values(test, step, obj):
    for key, value in test.iteritems():
        obj_value = obj.__dict__[key]
        if isinstance(obj_value, scheme.Column):
            obj_value = obj_value.get_value()

        if type(v) == list:
            values = value
            for value in values:
                if obj_value == value:
                    print '{}: {}.{} = "{}"'.format(step, obj._table_name, key, obj_value)
                    break
            else:
                raise ValueError('{}: {}.{} = "{}" -- needs on of these values: "{}"'.format(step, obj._table_name, key, obj_value, '", "'.join(values)))
        else:
            if obj_value == value:
                print '{}: {}.{} = "{}"'.format(step, obj._table_name, key, obj_value)
            else:
                raise ValueError('{}: {}.{} = "{}" -- needs value: "{}"'.format(step, obj._table_name, key, obj_value, value))

######## test code

interface.call('config', 'set', key='download.rate_limit', value=0)
interface.call('config', 'set', key='download.overwrite', value='overwrite')

try:
    if "account" in test:
        interface.call('account', 'add', name=plugin, **test['account'])
        args, kwargs = event.wait_for_events(['account:initialized', 'account:initialize_error'], test['timeout']['account'])
        check_values(test, 'result_account', args[1])

    if "urls" in test:
        ids = interface.call('core', 'add_links', links=test['urls'])

        if test['type'] == 'decrypter':
            while True:
                args, kwargs = event.wait_for_events(['file:greenlet_stop', 'file:created', 'file:deleted'], test['timeout']['check'])
                if args[0] == 'file:created':
                    check_values(test, 'result_new', args[1])
                elif args[1].id in ids:
                    check_values(test, 'result_check', args[1])
                    break

        elif test['type'] == 'hoster':
            args, kwargs = event.wait_for_events(['file:greenlet_stop'], test['timeout']['check'])
            check_values(test, 'result_check', args[1])

            gevent.spawn_later(0.5, interface.call, 'core', 'accept_collected', id=args[1].package.id)
            args, kwargs = event.wait_for_events(['file:greenlet_stop'], test['timeout']['download'])
            check_values(test, 'result_download', args[1])

        else:
            raise ValueError('unknown test type: {}'.format(test['type']))

    exit(0)
except ValueError as e:
    print str(e), "..."
    exit(1)
