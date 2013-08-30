# -*- coding: utf-8 -*-
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

from __future__ import absolute_import

"""import os
import traceback

open_files = dict()

old_open = os.open
def _open(*args, **kwargs):
    fd = old_open(*args, **kwargs)
    open_files[fd] = (args[0], traceback.extract_stack())
    print "OPEN", args, kwargs, fd
    return fd
os.open = _open

old_fdopen = os.fdopen
def _fdopen(*args, **kwargs):
    f = old_fdopen(*args, **kwargs)

    class File(object):
        def __getattr__(self, key):
            return getattr(f, key)

        def close(self, *a, **kw):
            try:
                del open_files[args[0]]
            except KeyError:
                #print "!"*100, 'error closing', a, kw
                pass
            print "CLOSE", args, kwargs
            return f.close(*a, **kw)

    if args[0] not in open_files:
	    open_files[args[0]] = (args[0], traceback.extract_stack())
    print "FDOPEN", args, kwargs
    return File()
os.fdopen = _fdopen

old_close = os.close
def _close(*args, **kwargs):
    try:
        del open_files[args[0]]
    except KeyError:
        #print "!"*100, 'error closing', args, kwargs
        pass
    print "CLOSE", args, kwargs
    return old_close(*args, **kwargs)
os.close = _close

def printr():
    if not open_files:
        print "no open files"
    print "-"*50, 'OPEN FILES'
    for k, (v, tb) in open_files.items():
        tb = ''.join(traceback.format_list(tb))
        if 'subprocess.py' in tb:
        	continue
        print k, v
        print tb
        print "-"*50
    print "-"*50, 'OPEN FILES END'

import gevent
def foo():
    while True:
        printr()
        gevent.sleep(60)
gevent.spawn(foo)
import atexit
atexit.register(printr)"""

import sys
from . import loader

def main():
    try:
        loader.init()
        loader.main_loop()
    except:
        loader.terminate()
    sys.exit(0)
    
if __name__ == "__main__":
    main()
