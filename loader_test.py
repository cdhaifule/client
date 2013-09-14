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

import sys

def main():
    if sys.platform == 'win32':
        import loader_win32 as loader
    elif sys.platform == 'darwin':
        import loader_darwin as loader
    else:
        import loader_linux2 as loader

    try:
        from client import test
    except ImportError:
        from client import testdefault as test

    test.init()
    loader.main()

if __name__ == '__main__':
    main()
