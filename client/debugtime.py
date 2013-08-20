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
import resource

from gevent import greenlet, getcurrent

times = dict()

def get_cpu():
    return sum(resource.getrusage(resource.RUSAGE_SELF)[0:1])

global_t = time.time()

class Timeit(object):
    def __init__(self, ctx, treshold=0, autoreport=True):
        self.ctx = ctx
        self.treshold = treshold

        self.tt = 0
        self.tcpu = 0

        self._global_t = None
        self._global_cpu = None

        self.autoreport = autoreport

    def report(self, force=False):
        if force or int(self.tcpu) >= self.treshold:
            gt = (time.time() - global_t)*1000
            gcpu = get_cpu()
            pt = (self.tt/gt)*100000
            pcpu = (self.tcpu/gcpu)*100
            print "--- TIMEIT: {:15.0f} global time, {:15.0f} global cpu, {:15.0f} time, {:15.0f} cpu, {:5.2f}% time, {:5.2f}% cpu, {}".format(gt*100, gcpu*100, self.tt*100, self.tcpu*100, pt, pcpu, self.ctx)

    def switch_in(self):
        self.cpu = get_cpu()
        self.t = time.time()

    def switch_out(self):
        self.tt += time.time() - self.t
        self.tcpu += get_cpu() - self.cpu

    def __enter__(self):
        self.gid = id(getcurrent())
        if not times:
            greenlet.greenlet.settrace(trace)
        times[self.gid] = self
        self.switch_in()

    def __exit__(self, *args):
        self.switch_out()
        try:
            del times[self.gid]
        except KeyError:
            pass
        if not times:
            greenlet.greenlet.settrace(None)
        if self.autoreport:
            self.report()

def time_func(func):
    def fn(*args, **kwargs):
        with Timeit(func):
            return func(*args, **kwargs)
    return fn

def trace(what, (origin, target)):
    if id(origin) in times:
        times[id(origin)].switch_out()
    if id(target) in times:
        times[id(target)].switch_in()
