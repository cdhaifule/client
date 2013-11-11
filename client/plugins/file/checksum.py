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
import traceback

from functools import partial

from Crypto.Cipher import AES

from ... import fileplugin
from ...scheme import intervalled
from ...contrib.mega import crypto

name = 'checksum'
priority = 50
config = fileplugin.config.new(name)
config.default('enabled', True, bool)

algorithms = set(hashlib.algorithms)


class ProgressTrack(intervalled.Cache):
    def __init__(self, check, file, total):
        self.check = check
        self.updated = 0
        self.total = total
        self.file = file
        self.start()
        
    def start(self):
        # setup file state that it is checked
        # self.file.init_progress(self.total)
        print "start checksum progress"

    def commit(self):
        #self.file.set_progress(self.updated)
        print "update checksum progress", self.updated
        
    def update(self, data):
        self.check.update(data)
        self.updated += len(data)
        
    def close(self):
        # update end state in file
        if self.check.name == "cbcmac":
            file_mac = crypto.str_to_a32(self.check.file_mac)
            if (file_mac[0] ^ file_mac[1], file_mac[2] ^ file_mac[3]) != self.check.meta_mac:
                self.file.fatal('file checksum invalid')
        elif self.check.hexdigest() != self.file.hash_value.lower():
            print self.file.get_download_file()
            print "Checked:", self.check.hexdigest()
            print "Set:", self.file.hash_value
            self.file.fatal('file checksum invalid')
        print "checksum OK"


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


class MegaCbcMac(object):
    """interface for checking cbc-mac of mega.co.nz"""
    name = "cbcmac"
    digest_size = 4
    block_size = 16
    
    def __init__(self, file_key):
        file_key = crypto.base64_to_a32(file_key)
        key = (file_key[0] ^ file_key[4], file_key[1] ^ file_key[5],
               file_key[2] ^ file_key[6], file_key[3] ^ file_key[7])
        self.meta_mac = file_key[6:8]
        self.file_mac = '\0' * 16
        self.key = crypto.a32_to_str(key)
        self.aes = AES.new(self.key, mode=AES.MODE_CBC, IV=self.file_mac)
        self.IV = crypto.a32_to_str([file_key[4], file_key[5]]*2)
    
    def update(self, chunk):
        enc = AES.new(self.key, mode=AES.MODE_CBC, IV=self.IV)
        for i in xrange(0, len(chunk)-16, 16):
            enc.encrypt(buffer(chunk, i, 16))
        try:
            i += 16
        except NameError:
            i = 0
        self.file_mac = self.aes.encrypt(enc.encrypt(chunk[i:].ljust(16, '\0')))
        
algorithms.add("crc32")
algorithms.add("cbc_mac_mega")
hashlib.crc32 = CRC32


def hashfile(path, check, bs=64*1024):
    try:
        with open(path) as f:
            if check.check.name == "cbcmac":
                chunks = crypto.get_chunks(path.st_size)

                def readfunc():
                    try:
                        return f.read(chunks.next()[1])
                    except StopIteration:
                        return ""
            else:
                readfunc = partial(f.read, bs)
            for data in iter(readfunc, ""):
                if not data:
                    break
                check.update(data)
    except (TypeError, ValueError):
        traceback.print_exc()


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
            if file.hash_type == "cbc_mac_mega":
                hash_func = MegaCbcMac(file.hash_value)
            else:
                hash_func = getattr(hashlib, file.hash_type)()
        else:
            hash_func = hashlib.new(file.hash_type)

        with ProgressTrack(hash_func, file, size) as tracker:
            threadpool.spawn(hashfile, path, tracker).wait()
            tracker.close()
