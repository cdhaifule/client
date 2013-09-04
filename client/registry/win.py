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
import sys
import bisect
import webbrowser
import subprocess

import _winreg as winreg
from _winreg import HKEY_CLASSES_ROOT, HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER, KEY_QUERY_VALUE, REG_SZ, KEY_ALL_ACCESS

from contextlib import contextmanager

from .. import settings, input, event, login
from ..config import globalconfig

config = globalconfig.new('registry.win')
config.default('webbrowser', None, unicode)

@contextmanager
def open_key(hkey, *args):
    key = winreg.OpenKeyEx(hkey, *args)
    yield key
    winreg.CloseKey(key)

@contextmanager
def create_key(hkey, subkey):
    key = winreg.CreateKey(hkey, subkey)
    yield key
    winreg.CloseKey(key)
    
def read_reg_key(hkey, subkey, value=""):
    try:
        with open_key(hkey, subkey, 0, KEY_QUERY_VALUE) as k:
            return winreg.QueryValueEx(k, value)
    except WindowsError as e:
        errno, message = e.args
        if errno != 2:
            raise e    
    return (None, None)

def enum_reg_keys(hkey, subkey):
    with open_key(hkey, subkey) as k:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(k, i)
            except:
                break
            yield name
            i += 1

def set_default_open_file():
    name = "download.am-file.add"
    #ty:
    #    open_dl_am
    #pa
    
def remove_protocol(proto):
    try:
        with open_key(HKEY_CLASSES_ROOT, r"{}\shell\open".format(proto), 0, KEY_ALL_ACCESS) as key:
            winreg.DeleteKey(key, "command")
        return True
    except WindowsError:
        pass

def register_protocol(proto):
    command = '"{}\\download.am.exe" "core.add_links links=%1"'.format(settings.app_dir)
    subkey = r"{}\shell\open\command".format(proto)
    try:
        text = read_reg_key(HKEY_CLASSES_ROOT, subkey)
    except:
        change_needed = True
    else:
        if text != command:
            change_needed = True
        else:
            change_needed = False
    
    if change_needed:
        # xxx ask if i may change the registry
        remove_protocol(proto)
        for subkey, value, data in [
            (proto, "", "URL:{} protocol".format(proto)),
            (proto, "URL Protocol", ""),
            (subkey, "", command)]:
            with create_key(HKEY_CLASSES_ROOT, subkey) as key:
                winreg.SetValueEx(key, value, 0, REG_SZ, data)


browser_translate = {
    'FIREFOX.EXE': 'Mozilla Firefox',
    'IEXPLORE.EXE': 'Internet Explorer',
    'OperaStable': 'Opera',
    'OperaNext': 'Opera Next'
}
browser_priority = ['chromium', 'chrome', 'opera', 'firefox']


def _parse_browser_path(path):
    try:
        if path.startswith('"'):
            path = path[1:].split('"', 1)[0]
        return path
    except:
        return None

def get_default_browser():
    return _parse_browser_path(read_reg_key(HKEY_CLASSES_ROOT, 'http\\shell\\open\\command')[0])

def get_browser_path(key):
    return _parse_browser_path(read_reg_key(HKEY_LOCAL_MACHINE, 'SOFTWARE\\Clients\\StartMenuInternet\\{}\\shell\\open\\command'.format(key))[0])

def iterate_browsers(default=None):
    if default is None:
        default = get_default_browser() or ''
    default = default.lower()
    for key in enum_reg_keys(HKEY_LOCAL_MACHINE, 'SOFTWARE\\Clients\\StartMenuInternet'):
        name = browser_translate.get(key, key)
        path = get_browser_path(key)
        if not path:
            continue
        if key == 'IEXPLORE.EXE':
            version = int(read_reg_key(HKEY_LOCAL_MACHINE, 'Software\\Microsoft\\Internet Explorer', 'Version')[0].split('.', 1)[0])
            if version < 9:
                outdated = True
            else:
                outdated = False
        elif key == 'OperaStable':
            outdated = True
        else:
            outdated = False

        yield key, name, path, path.lower() == default, outdated


@event.register('registry:select_browser')
def on_select_browser(e, open_browser):
    result = ask_user(False)
    if open_browser and result:
        webbrowser.open_new_tab(login.get_sso_url())

class DLAMBrowser(webbrowser.BaseBrowser):
    def open(self, url, new=0, autoraise=True):
        for key, name, path, default, outdated in iterate_browsers():
            if config.webbrowser is None and default:
                break
            elif key == config.webbrowser:
                break
        else:
            path, outdated = None, False
        if path is None or outdated:
            if not ask_user(outdated and name):
                return
            return self.open(url, new, autoraise)
        subprocess.Popen([path, url]) # TODO: this can raise an unicode error on win32

def ask_user(outdated):
    elements = list()
    if outdated:
        elements.append([input.Text(['Your current default browser #{browser} is not compatible with Download.am.', dict(browser=outdated)])])
    values = list()
    default_value = None
    for key, name, path, default, outdated in iterate_browsers():
        if not outdated:
            for i, p in enumerate(browser_priority):
                if p in name.lower():
                    priority = i
                    break
            else:
                priority = 999
            if key == config.webbrowser:
                default_value = key
            elif config.webbrowser is None and default:
                default_value = key
            bisect.insort(values, (priority, (key, name)))
    values = [v for p, v in values]
    if values:
        elements.append([input.Text('Please select a browser you like to use with Download.am.')])
        elements.append([input.Text('')])
        elements.append([input.Float('left')])
        elements.append([input.Radio('browser', value=values, default=default_value)])
        elements.append([input.Float('center')])
        elements.append([input.Text('')])
        elements.append([input.Submit('OK')])
        try:
            result = input.get(elements, type='browser_select', timeout=None, close_aborts=True, ignore_api=True)
            config.webbrowser = result['browser']
            return True
        except:
            if outdated:
                config.webbrowser = values[0][0]
                return True
        return False
    else:
        if outdated:
            elements.append([input.Text('')])
        elements.append([input.Text('You have no compatible webbrowser installed.')])
        elements.append([input.Text('The best choice is Chrome, Firefox or Opera Next. You find the download links below.')])
        elements.append([input.Text('')])
        elements.append([input.Link('https://www.google.com/chrome/', 'Google Chrome - https://www.google.com/chrome/')])
        elements.append([input.Link('https://www.mozilla.org/‎', 'Mozilla Firefox - https://www.mozilla.org/')])
        elements.append([input.Link('http://www.opera.com/computer/next‎', 'Opera Next - http://www.opera.com/computer/next')])
        elements.append([input.Text('')])
        elements.append([input.Submit('OK')])
        webbrowser._browsers = _old_browsers
        webbrowser._tryorder = _old_tryorder
        try:
            input.get(elements, type='browser_download', timeout=None, close_aborts=True, ignore_api=True)
        except:
            pass
        finally:
            webbrowser._browsers = {}
            webbrowser._tryorder = []
            webbrowser.register('dlam-default', DLAMBrowser)
        return False

_old_browsers = webbrowser._browsers
_old_tryorder = webbrowser._tryorder

webbrowser._browsers = {}
webbrowser._tryorder = []
webbrowser.register('dlam-default', DLAMBrowser)
