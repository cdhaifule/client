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

import threading
threading._DummyThread._Thread__stop = lambda x: 42

import sys
import os
import traceback

devfile = os.path.join(os.getenv("HOME", ""), ".download.am.dev")
try:
    with open(devfile) as f:
        for p in f:
            sys.path.insert(0, p.strip())
except:
    if os.path.exists(devfile):
        traceback.print_exc()

from client import app

def main():
    app.main()


if __name__ == '__main__':
    main()
