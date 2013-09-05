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
import webbrowser

from . import interface, settings, login
from .config import globalconfig

config = globalconfig.new('browser')
config.default('home_dir', settings.home_dir, unicode)
config.default('script_dir', settings.script_dir, unicode)
config.default('path_seperator', os.sep, unicode)

@interface.register
class BrowserInterface(interface.Interface):
    name = "browser"

    def ls(path):
        result = []
        if sys.platform == 'win32' and len(path) == 2 and path[1] == ':':
            path += '\\'
        if sys.platform == 'win32' and path == "\\" or path == "/" or not path:
            import win32api
            return path, [dict(path=f, 
                read=os.access(f, os.R_OK), 
                write=os.access(f, os.W_OK)) 
            for f in win32api.GetLogicalDriveStrings().split('\0')[:-1]]
        try:
            for f in os.listdir(path):
                f = os.path.join(path, f)
                if not os.path.isdir(f):
                    continue
                result.append(dict(path=f, read=os.access(f, os.R_OK), write=os.access(f, os.W_OK)))
            return path, result
        except (OSError, IOError) as e:
            return "ls.error", repr(e)
    def mkdir(path=None, name=None):
        npath = os.path.join(path, name)
        if not os.path.exists(npath):
            try:
                os.makedirs(npath)
            except (OSError, IOError) as e:
                return "mkdir.error", repr(e)
        return interface.call('browser', 'ls', path=path)

    def open_browser():
        webbrowser.open_new_tab(login.get_sso_url())

def init():
    pass
