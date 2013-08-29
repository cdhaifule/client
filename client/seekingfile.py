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
import sys
import traceback

from gevent.lock import Semaphore
from gevent.threadpool import ThreadPool

try:
    import win32file, win32api
    def get_free_space(path):
        try:
            path = os.path.dirname(path)
            return win32file.GetDiskFreeSpaceEx(path)[0]
        except OSError:
            traceback.print_exc()
            return

    def allocate_file(path, size):
        handle = win32file.CreateFile(path, win32file.GENERIC_WRITE, 
            win32file.FILE_SHARE_WRITE, None, 
            win32file.CREATE_ALWAYS, 0, None)
        win32file.SetFilePointer(handle, size, win32file.FILE_BEGIN)
        win32file.SetEndOfFile(handle)
        win32api.CloseHandle(handle)
except ImportError:
    def get_free_space(path):
        try:
            path = os.path.dirname(path)
            st = os.statvfs(path)
            return st.f_bavail * st.f_frsize
        except OSError:
            traceback.print_exc()
            return
            
    def allocate_file(path, size):
        f = os.open(path, os.O_CREAT|os.O_RDWR)
        os.lseek(f, size - 1, os.SEEK_SET)
        os.write(f, b'\x00')
        os.close(f)

def check_space(path, size):
    free = get_free_space(path)
    if free is None:
        return
    if free < (size + 100*1024*1024):
        raise IOError(28, "Disk is full.")
    

open_lock = Semaphore()
allocate_pool = ThreadPool(1)

class SeekingFile:
    def __init__(self, filepath, size=None):
        self.filepath = filepath
        self.size = size

        self.lock = Semaphore()
        self.refcount = 0
        self.pos = 0
        self.f = None

    def __del__(self):
        if self.f:
            os.close(self.f)

    def write(self, data, pos):
        with self.lock:
            if self.pos != pos:
                os.lseek(self.f, pos, os.SEEK_SET)
            os.write(self.f, data)
            self.pos = pos + len(data)

    def open(self):
        with open_lock, self.lock:
            if self.refcount == 0:
                exists = os.path.exists(self.filepath)
                if not exists and self.size and self.size > 1:
                    check_space(self.filepath, self.size)
                    allocate_pool.apply(allocate_file, (self.filepath, self.size))
                flags = os.O_RDWR | os.O_CREAT
                if sys.platform == "win32":
                    flags |= os.O_BINARY | os.O_RANDOM
                if hasattr(os, 'O_NOATIME'):
                    flags |= os.O_NOATIME
                self.f = os.open(self.filepath, flags)
                self.pos = None
            self.refcount += 1
            return self

    def close(self):
        with self.lock:
            self.refcount -= 1
            if self.refcount == 0:
                os.fsync(self.f)
                os.close(self.f)
                self.f = None

    def __enter__(self):
        return self.open()

    def __exit__(self, type, value, traceback):
        self.close()
