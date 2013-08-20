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

import time
import gevent

from gevent.event import AsyncResult

from . import interface, logger, api
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
    def __init__(self, livetime=600):
        self.livetime = livetime
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
                del self.timeouts[key]
                dict.__delitem__(self, key)
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
