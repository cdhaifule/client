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

import zipfile

from ... import core

name = 'zipextract'
priority = 100


def match(path, file):
    if core.config.autoextract and path.ext == "zip":
        return True

  
def process(path, file, hddsem, threadpool):
    ball = zipfile.ZipFile(path)
    with hddsem:
        if file is None:
            extract = path.dir
        else:
            extract = file.get_extract_path()
        threadpool.spawn(ball.extractall, extract).wait()  # xxx flat unpack, xxx progress?
