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
import time
import bisect
import gevent
import hashlib

from gevent.event import AsyncResult

from . import interface, logger, api, settings
from .config import globalconfig

config = globalconfig.new('cache')
config.default('get_timeout', 3, int)

log = logger.get('cache')

get_results = dict()

def get(items):
    if not api.client.is_connected():
        return dict()

    result = AsyncResult()
    rid = id(result)
    get_results[rid] = result
    api.proto.send('backend', command='cache.get', payload=dict(id=rid, items=items), encrypt=False)

    try:
        return result.get(timeout=config.get_timeout)
    finally:
        del get_results[rid]

def set(cache):
    api.proto.send('backend', command='cache.set', payload=cache, encrypt=False)

@interface.register
class Interface(interface.Interface):
    name = 'cache'

    def get(id=None, items=None):
        if id not in get_results:
            log.warning('get_cache result {} not found'.format(id))
            return 'request not found'
        get_results[id].set(items)


class CachedDict(dict):
    def __init__(self, livetime=600, callback=None):
        self.livetime = livetime
        self.callback = callback
        self.timeouts = dict()
        self.greenlet = None

    def set_livetime(self, livetime):
        self.livetime = livetime

    def _set_timeout(self, key):
        t = time.time() + self.livetime
        self.timeouts[key] = t
        if self.greenlet is not None and self.greenlet.eta > t:
            self.greenlet.kill()
        self.greenlet = gevent.spawn_later(self.livetime, self._cleanup)
        self.greenlet.eta = t

    def _cleanup(self):
        self.greenlet = None
        t = time.time()
        smallest = None
        for key, value in self.timeouts.items():
            if value < t:
                if self.callback:
                    item = dict.__getitem__(self, key)
                del self.timeouts[key]
                dict.__delitem__(self, key)
                if self.callback:
                    self.callback(item)
            elif smallest is None or smallest > value:
                smallest = value
        if smallest is not None:
            self.greenlet = gevent.spawn_later(smallest - t, self._cleanup)
            self.greenlet.eta = t

    def __setitem__(self, key, value):
        self._set_timeout(key)
        dict.__setitem__(self, key, value)

    def __getitem__(self, key):
        self._set_timeout(key)
        return dict.__getitem__(self, key)

    def __delitem__(self, key):
        del self.timeouts[key]
        dict.__delitem__(self, key)
        if not self and self.greenlet:
            self.greenlet.kill()
            self.greenlet = None


def sha256(s):
    if isinstance(s, unicode):
        s = s.encode("utf-8")
    return hashlib.sha256(s).hexdigest()

class LRUFileCache(object):
    def __init__(self, path, levels=0, expire_time=None, max_size=None, buffer_size=None, max_items=None, buffer_items=None, cleanup_timeout=60):
        """file based LRU cache
        path - cache base directory
        levels - cache levels (<path>/1/2/3/123456789)
        expire_time - time of cache expire in seconds
        max_size - maximal bytes used by cache
        buffer_size - on cleanup remove items until we have max_size - buffer_size free bytes
        buffer_items - on cleanup remove items until we have max_items - buffer_items of free items
        cleanup_timeout - when max_size or max_items is reached wait cleanup_timeout seconds till cleanup start
        """
        if max_size is not None and buffer_size is not None and buffer_size > max_size:
            raise RuntimeError('buffer_size must be smaller than max_size')
        if max_items is not None and buffer_items is not None and buffer_items > max_items:
            raise RuntimeError('buffer_items must be smaller than max_items')

        self.path = path
        self.levels = levels
        self.expire_time = expire_time
        self.max_size = max_size
        self.buffer_size = buffer_size
        self.max_items = max_items
        self.buffer_items = buffer_items
        self.cleanup_timeout = cleanup_timeout

        self.size = 0
        self.items = 0
        
        self.check_greenlet = gevent.spawn(self._check)

    def _create(self, key):
        id = sha256(key)
        a = list()
        for l in xrange(self.levels):
            a.append(id[l:l + 1])
        path = os.path.join(self.path, *a)
        file = os.path.join(path, id)
        return id, path, file

    def _check_expired(self, file, remove_from_stats=True):
        if self.expire_time is not None:
            stat = os.stat(file)
            if stat.st_atime + self.expire_time < time.time():
                os.unlink(file)
                if remove_from_stats:
                    self.size -= stat.st_size
                    self.items -= 1
                raise KeyError()
            return stat

    def _check(self):
        max_size = self.max_size - (self.buffer_size or 0)
        max_items = self.max_items - (self.buffer_items or 0)
        try:
            clean = list()
            self.size = 0
            for root, dirs, files in os.walk(self.path):
                for file in files:
                    file = os.path.join(root, file)
                    try:
                        stat = self._check_expired(file, remove_from_stats=False)
                    except KeyError:
                        continue
                    self.size += stat.st_size
                    self.items += 1
                    if max_size is not None or max_items is not None:
                        bisect.insort(clean, (stat.st_atime, stat.st_size, file))
                gevent.sleep(0)
            if max_size is not None or max_items is not None:
                while clean and ((self.size is not None and self.size > max_size) or (max_items is not None and len(clean) > max_items)):
                    atime, size, file = clean.pop(0)
                    os.unlink(file)
                    self.size -= size
        finally:
            self.check_greenlet = None

    def __getitem__(self, key):
        id, path, file = self._create(key)
        if not os.path.exists(file):
            raise KeyError(key)
        try:
            self._check_expired(file)
        except KeyError:
            raise KeyError(key)
        os.utime(file, None)
        with open(file, 'rb') as f:
            return f.read()

    def __setitem__(self, key, value):
        id, path, file = self._create(key)
        if not os.path.exists(path):
            os.makedirs(path)
        elif os.path.exists(file):
            self.size -= os.path.getsize(file)
            self.items -= 1
        self.size += len(value)
        self.items += 1
        if self.cleanup_timeout is not None and self.check_greenlet is None and ((self.max_size is not None and self.size > self.max_size) or (self.max_items is not None and self.items > self.max_items)):
            self.check_greenlet = gevent.spawn_later(self.cleanup_timeout, self._check)
        with open(file, 'wb') as f:
            f.write(value)

lru_file_cache = LRUFileCache(os.path.join(settings.temp_dir, 'lru'), 2, 14*24*3600, 50*1024*1024, 10*1024*1024, 100000, 20000)
