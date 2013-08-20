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

import json

try:
    from pyv8 import PyV8
except ImportError:
    import PyV8
    
pyv8 = PyV8

def execute(code):
    if isinstance(code, unicode):
        code = code.encode("utf-8")
    with PyV8.JSContext() as c:
        c.enter()
        return c.eval(code)

def _convert(data):
    result = {}
    for key in data.keys():
        if isinstance(data[key], PyV8.JSObject):
            result[key] = _convert(data[key])
        else:
            result[key] = data[key]
    return result

def loads(data):
    s = execute("JSON.stringify({})".format(data))
    return json.loads(s)
