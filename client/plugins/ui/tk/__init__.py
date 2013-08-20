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

import re
import sys
import gevent

from . import input
from .splash import Splash

def init():
    if sys.platform == "win32":
        from ..systray import win
        win.init()

def main_loop():
    gevent.run()

def get_active_window_title():
    import subprocess
    
    root_check = ''
    root = subprocess.Popen(['xprop', '-root'],  stdout=subprocess.PIPE)

    if root.stdout != root_check:
        root_check = root.stdout

        for i in root.stdout:
            if '_NET_ACTIVE_WINDOW(WINDOW):' in i:
                id_ = i.split()[4]
                id_w = subprocess.Popen(['xprop', '-id', id_], stdout=subprocess.PIPE)
        id_w.wait()
        buff = []
        for j in id_w.stdout:
            buff.append(j)

        for line in buff:
            match = re.match(r'WM_NAME\((?P<type>.+)\) = "?(?P<name>.+)"?', line)
            if match is not None:
                type = match.group("type")
                if type == "STRING" or type == "COMPOUND_TEXT":
                    return match.group("name")


def browser_has_focus():
    if sys.platform == "win32":
        import win32gui
        wnd = win32gui.GetForegroundWindow()
        if wnd:
            title = win32gui.GetWindowText(wnd)
            if title.startswith('Download.am'):
                return wnd
    else:
        title = get_active_window_title()
        if title.startswith('Download.am'):
            return True
    return False

def browser_to_focus(has_focus=None):
    if has_focus is None:
        has_focus = browser_has_focus()
    if sys.platform == "win32":
        import win32gui
        win32gui.SetForegroundWindow(has_focus)
        return True
    return False
