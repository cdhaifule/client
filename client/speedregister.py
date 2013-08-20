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

from collections import deque
from gevent.coros import Semaphore

from .config import globalconfig

config = globalconfig.new("speed")
config.default("default_precision", 0.5, float)
config.default("default_interval", 10, int)
config.default("default_track", 60, int)
 
class SpeedRegister(object):
    def __init__(self, minimal_precision=None, max_track=None):
        if minimal_precision is None:
            minimal_precision = config.default_precision
        if max_track is None:
            max_track = config.default_track
        self.min_prec = minimal_precision
        self.max_track = max_track
        self.registred = deque()
        self.lock = Semaphore(1)
    
    def reset(self):
        self.registred = deque()

    def register(self, bytes):
        with self.lock:
            now = time.time()
            if self.registred and (now - self.registred[-1][0]) < self.min_prec:
                self.registred[-1][1] += bytes
            else:
                self.registred.append([now, bytes])
        self._cleanup()

    def get_bytes(self, interval=None):
        """return how much bytes were registred in the last `interval` seconds"""
        if interval is None:
            interval = config.default_interval
        stoptime = time.time() - interval
        mintime = 0
        bytes = 0
        for t, byte in reversed(self.registred):
            if t < stoptime:
                break
            else:
                mintime = t
                bytes += byte
        maxtime = self.registred and self.registred[-1][0] or 0
        return bytes/(maxtime - mintime + 1)

    def _cleanup(self):
        with self.lock:
            now = time.time()
            to_pop = 0
            for t, _ in self.registred:
                if now - t > self.max_track:
                    to_pop += 1
                else:
                    break
            for i in xrange(to_pop):
                self.registred.popleft()
 
    def _run_cleanup(self):
        while 1:
            gevent.sleep(self.max_track)
            self._cleanup()
 
    def start_cleanup(self):
        gevent.spawn(self._run_cleanup)

globalspeed = SpeedRegister()
