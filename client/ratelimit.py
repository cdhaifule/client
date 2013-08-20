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

import time
import gevent
import requests

from . import event, logger, speedregister

log = logger.get('ratelimit')

class Bucket(object):
    sleepmax = 2
    
    def __init__(self, rate=0):
        """`rate` in bytes per second"""
        self.set_rate(rate)
        
    def set_rate(self, rate=0):
        self.rate = rate
        self.int_rate = None
        self.currenttime = time.time()
        self.filled = 0
        
    def register(self, size):
        """register size bytes, returns time to sleep
        """
        rate = self.int_rate or self.rate
        if not rate:
            return 0
        
        if self.filled < rate:
            now = time.time()
            self.filled += rate * (now-self.currenttime)
            if self.filled > rate:
                # never fill more than rate
                self.filled = rate
            self.currenttime = now
        
        # drink
        self.filled -= size
        
        if self.filled < 0:
            # too much, sleep
            return float(-self.filled) / rate
        return 0
        
    def sleep(self, size, subtract=0, sleepfunc=gevent.sleep):
        tosleep = self.register(size) - subtract
        if tosleep <= 0:
            sleepfunc(0)
        elif tosleep <= self.sleepmax:
            sleepfunc(tosleep)
        else:
            oldrate = self.int_rate or self.rate
            while tosleep > 0 and oldrate == (self.int_rate or self.rate):
                s = min(self.sleepmax, tosleep)
                sleepfunc(s)
                tosleep -= s

default_bucket = Bucket()

set_rate = default_bucket.set_rate
sleep = default_bucket.sleep
register = default_bucket.register

checker = None
class RateChecker(object):
    def __init__(self, bucket, speed, check_interval=5, reset_interval=600, limit_percent=0.95):
        self.bucket = bucket
        self.speed = speed
        self.check_interval = check_interval
        self.reset_interval = reset_interval
        self.limit_percent = limit_percent

        self.init = None
        self.last_reset = time.time()
        self.greenlet = None
        self.bucket.ini_rate = None

    def start(self):
        if self.greenlet is None:
            self.greenlet = gevent.spawn(self.check)

    def stop(self):
        if self.greenlet is not None:
            self.greenlet.kill()
            self.greenlet = None

            self.init = None
            self.last_reset = time.time()
            self.bucket.ini_rate = None

    def benchmark(self):
        s = 0
        for i in xrange(3):
            try:
                t = time.time()
                with gevent.Timeout(2):
                    resp = requests.get('https://eu-static-dlme.s3-external-3.amazonaws.com/assets/raw/ping.txt')
                    if resp.text != 'pong':
                        log.warning('got response {}'.format(resp.text))
                s += time.time() - t
            except:
                s += 2
        return s/(i + 1)

    def check(self):
        try:
            if self.last_reset + self.reset_interval < time.time():
                self.init = None
                self.last_reset = time.time()
                self.bucket.ini_rate = None
                #print "!"*100, 'complete reset'

            t = self.benchmark()
            if self.init is None or t < self.init:
                self.init = t
                #print "!"*100, 'BEST', self.init
            
            if t < self.init*1.5 and self.bucket.ini_rate is not None:
                self.bucket.ini_rate = None
                print "!"*100, 'reset', t

            if t > self.init*2.0:
                speed = self.speed.get_bytes()
                if speed > 8192:
                    self.bucket.int_rate = speed*self.limit_percent
                    print "!"*100, 'limit', t, int(speed), int(self.bucket.int_rate)
        finally:
            self.greenlet = gevent.spawn_later(self.check_interval, self.check)

def init():
    global checker
    checker = RateChecker(default_bucket, speedregister.globalspeed)

    @event.register('download:started')
    @event.register('torrent:started')
    def on_started(*args):
        #checker.start()
        pass
        
    @event.register('download:stopped')
    def on_stopped(*args):
        from . import download, torrent
        if download.config.state == 'stopped' and torrent.config.state == 'stopped':
            #checker.stop()
            pass
