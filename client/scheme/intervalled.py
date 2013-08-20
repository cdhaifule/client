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

from .transaction import transaction

from .. import event, interface

from gevent.lock import Semaphore

lock = Semaphore()
objects = []
config = None

@event.register('config:loaded')
def setconfig(e):
    from ..config import globalconfig
    config = globalconfig.new("intervalled")
    config.default("commit", 1.0, float)

class Cache(object):
    def __enter__(self):
        objects.append(self)
        event.fire_once_later(config and config["commit"] or 1.0, 'scheme.intervalled:commit')
    enter = __enter__

    def __exit__(self, *args):
        objects.remove(self)
        with transaction:
            self.commit()
    exit = __exit__

    def commit(self):
        raise NotImplementedError()

@event.register('scheme.intervalled:commit')
def commit(e):
    try:
        with lock, transaction:
            for cache in objects:
                cache.commit()
    finally:
        if objects:
            event.fire_once_later(config and config["commit"] or 1.0, 'scheme.intervalled:commit')

@interface.register
class IntervalledInterface(interface.Interface):
    name = 'intervalled'

    def set_interval(seconds=None):
        if seconds>0:
            with transaction:
                config["commit"] = float(seconds)
