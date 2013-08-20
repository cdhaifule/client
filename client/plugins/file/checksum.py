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

import hashlib
import zlib
import struct

from functools import partial

import gevent

from ... import fileplugin

name = 'checksum'
priority = 50
config = fileplugin.config.new(name)
config.default('enabled', True, bool)

algorithms = set(hashlib.algorithms)

class ProgressTrack(object):
    def __init__(self, check, file, total):
        self.check = check
        self.updated = 0
        self.total = total
        self.file = file
        self.async = gevent.get_hub().loop.async()
        self.async.start(self.update_file)
        self.start()
        
    def start(self):
        # setup file state that it is checked
        self.file.init_progress(self.total)
        
    def update_file(self):
        self.file.set_progress(self.updated)
        
    def update(self, data):
        self.check.update(data)
        self.updated += len(data)
        self.async.send() # runs in thread, async updates call update_file
        
    def close(self):
        # update end state in file
        if self.check.hexdigest() != self.file.hash_value:
            print "Checked:", self.check.hexdigest()
            print "Set:", self.file.hash_value
            self.file.fatal('error while checking file checksum')
            
class CRC32(object):
    """hashlib compatible interface to crc32"""
    name = 'crc32'
    digest_size = 4
    block_size = 64
    def __init__(self, init=""):
        self._hash = 0
        self.update(init)
        
    def digest(self):
        return struct.pack("I", self._hash & 0xffffffff)
        
    def hexdigest(self):
        return "{:08x}".format(self._hash & 0xffffffff)
        
    def update(self, bytes):
        self._hash = zlib.crc32(bytes, self._hash)
        
algorithms.add("crc32")
hashlib.crc32 = CRC32

def hashfile(path, check, bs=64*1024):
    with open(path) as f:
        for data in iter(partial(f.read, bs), ""):
            check.update(data)

def match(path, file):
    if file is None:
        return False
    if not config.enabled:
        return False
    if file.hash_type:
        if file.hash_type in algorithms:
            return True
        try:
            hashlib.new(file.hash_type)
            return True
        except ValueError:
            file.log.debug("hashtype {} not supported".format(file.hash_type))
            return False
    return False

def process(path, file, hddsem, threadpool):
    with hddsem:
        size = path.st_size
        if file.hash_type in algorithms:
            hash_func = getattr(hashlib, file.hash_type)()
        else:
            hash_func = hashlib.new(file.hash_type)
        tracker = ProgressTrack(hash_func, file, size)
        threadpool.spawn(hashfile, path, tracker).wait()
        tracker.close()
