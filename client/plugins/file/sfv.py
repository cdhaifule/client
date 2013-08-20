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

"""
sfv.py

Created on 2013-04-22.

Add crc32 hashes from a sfv file
"""

import gevent
import os
import re

from ... import core

name = "sfv"
priority = 90

@core.File.name.setter
def on_name(file, name):
    if name is None:
        return name
    if name.endswith(".sfv"):
        file.weight = 10
    else:
        if file.weight == 10:
            file.weight = 0
    return name

def match(path, file):
    return path.ext == "sfv"
    
def process(path, file):
    sfvpath = file.get_complete_path()
    paths = dict()
    with open(path) as f:
        for line in f:
            if line.startswith(';'):
                continue
            try:
                filename, crc32 = line.strip().rsplit(" ", 1)
                crc32 = crc32.lower()
            except ValueError:
                continue
            else:
                if not re.match("[0-9a-f]{8}", crc32):
                    continue
            paths[os.path.join(sfvpath, filename)] = crc32
            gevent.sleep(0)
            
    for f in core.files():
        path = f.get_complete_file()
        if path in paths:
            with core.transaction:
                f.hash_type = "crc32"
                f.hash_value = paths[path]
