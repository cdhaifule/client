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

from collections import deque

import gevent
from gevent.event import AsyncResult

events = dict()

once = {}

debug = False # on true log all events

def add(event, func):
    if event not in events:
        events[event] = [func]
    else:
        events[event].append(func)
    
def register(event):
    """decorator"""
    def f(func):
        add(event, func)
        return func
    return f

def remove(event, func):
    try:
        events[event].remove(func)
    except (KeyError, ValueError):
        pass

def _fire(se):
    if debug:
        print se
    if se[0] not in events:
        return
    for e in events[se[0]]:
        try:
            e(se[0], *se[1], **se[2])
        except:
            raise

def _fire_once(se):
    del once[se[0]]
    _fire(se)

def fire(event, *args, **kwargs):
    if event in events or debug:
        t = gevent.spawn(_fire, (event, args, kwargs))
        gevent.sleep(0)
        return t

def fire_later(seconds, event, *args, **kwargs):
    se = (event, args, kwargs)
    t = gevent.spawn_later(seconds, _fire, se)
    return t

def fire_once(event, *args, **kwargs):
    if event in once:
        return
    if event in events or debug:
        once[event] = True
        se = (event, args, kwargs)
        t = gevent.spawn(_fire_once, se)
        return t

def fire_once_later(seconds, event, *args, **kwargs):
    if event in once:
        return
    once[event] = True
    se = (event, args, kwargs)
    t = gevent.spawn_later(seconds, _fire_once, se)
    return t

def wait_for_events(events, timeout=None):
    def _remove():
        for event in events:
            remove(event, on_event)

    def on_event(*args, **kwargs):
        _remove()
        result.set([args, kwargs])

    if timeout:
        timeout = gevent.Timeout(timeout, RuntimeError('timeout while waiting for events: {}'.format(', '.join(events))))
        timeout.start()
    try:
        result = AsyncResult()
        for event in events:
            add(event, on_event)
        return result.get()
    finally:
        if timeout:
            timeout.cancel()
        _remove()

class ThreadCaller(object):
    def __init__(self, func=None):
        self.func = func
        self.queue = deque()
        self.async = gevent.get_hub().loop.async()
        self.async.start(self.run)
        
    def __call__(self, *args, **kwargs):
        # call from thread
        if self.func is not None:
            self.queue.append((self.func, args, kwargs))
        else:
            self.queue.append((args[0], args[1:], kwargs))
        self.async.send()
        
    def run(self):
        while self.queue:
            func, args, kwargs = self.queue.popleft()
            try:
                gevent.spawn(func, *args, **kwargs)
            except:
                raise

call_from_thread = ThreadCaller()
