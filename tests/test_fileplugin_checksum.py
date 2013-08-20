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

import os

from client import fileplugin
from client import logger

class File(object):
    path = os.path.join(os.path.dirname(__file__), "test_fileplugin.zip")
    fatal_called = False
    log = logger.get("test checksum")
    def get_complete_file(self):
        return self.path
    def get_complete_path(self):
        return os.path.dirname(self.path)
    def fatal(self, msg=""):
        self.fatal_called = True
        print "fatal called", msg
    def init_progress(self, *args):
        pass
    def set_progress(self, *args):
        pass
        
class FakeSHA1File(File): # hashlib.sha1
    hash_type = "sha1"
    hash_value = "a7c72f95949717854dc06d553ed4f4e9940ddcca"
    
class FakeCRC32File(File): # self implemented crc32 interface
    hash_type = "crc32"
    hash_value = "d8b6184c"
    
class FakeRIPEMD160File(File): # hashlib.new
    hash_type = "ripemd160"
    hash_value = "3d48802d859f9ef3dc54665acd1cfb9f8073084b"
    
def main():
    fileplugin.init()
    for _, __, plugin in fileplugin.manager.plugins:
        if plugin.name == "checksum":
            break
    assert plugin.name == "checksum"
    tests = [FakeSHA1File(), FakeCRC32File(), FakeRIPEMD160File()]
    for t in tests:
        print "testing", t.hash_type
        path, file = fileplugin.fileorpath(t)
        assert plugin.match(path, file)
        fileplugin.manager.execute_plugin(plugin, path, file)
        assert not t.fatal_called
        print "\tok"

if __name__ == '__main__':
    main()

