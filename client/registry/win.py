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
import time
import gevent
import bisect
import tempfile
import webbrowser
import subprocess

import _winreg as winreg
from _winreg import HKEY_CLASSES_ROOT, HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER, KEY_QUERY_VALUE, REG_SZ, KEY_ALL_ACCESS, KEY_WRITE, KEY_CREATE_SUB_KEY, KEY_SET_VALUE

from contextlib import contextmanager
from gevent.lock import Semaphore

from .. import settings, input, event, login
from ..config import globalconfig

config = globalconfig.new('registry.win')
config.default('webbrowser', None, unicode, private=True)
config.default('portable', '', unicode, private=True)

config.default('autostart', True, bool, protected=True)
config.default('ext_torrent', True, bool, protected=True)
config.default('ext_wdl', True, bool, protected=True)
config.default('ext_dlc', True, bool, protected=True)
config.default('ext_ccf', True, bool, protected=True)
config.default('scheme_magnet', True, bool, protected=True)


################################################ basic registry functions

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


################################################ webbrowser

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
    result = _parse_browser_path(read_reg_key(HKEY_CURRENT_USER, 'Software\\Classes\\http\\shell\\open\\command')[0])
    if result is None:
        result = _parse_browser_path(read_reg_key(HKEY_CLASSES_ROOT, 'http\\shell\\open\\command')[0])
    return result

def get_browser_path(key):
    result = _parse_browser_path(read_reg_key(HKEY_CURRENT_USER, 'Software\\Clients\\StartMenuInternet\\{}\\shell\\open\\command'.format(key))[0])
    if result is None:
        result = _parse_browser_path(read_reg_key(HKEY_LOCAL_MACHINE, 'Software\\Clients\\StartMenuInternet\\{}\\shell\\open\\command'.format(key))[0])
    return result

def iterate_browsers(default=None):
    if default is None:
        default = get_default_browser() or ''
    default = default.lower()
    ignore = set()
    for hkey in (HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE):
        for key in enum_reg_keys(hkey, 'Software\\Clients\\StartMenuInternet'):
            if key in ignore:
                continue
            ignore.add(key)
            name = browser_translate.get(key, key)
            path = get_browser_path(key)
            if not path:
                continue
            if not os.path.exists(path):
                continue
            if key == 'IEXPLORE.EXE':
                version = int(read_reg_key(hkey, 'Software\\Microsoft\\Internet Explorer', 'Version')[0].split('.', 1)[0])
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
        if config.webbrowser == '_portable' and config.portable and os.path.exists(config.portable):
            path = config.portable
        else:
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
        subprocess.Popen([path.encode(sys.getfilesystemencoding()), url.encode(sys.getfilesystemencoding())])

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

    if config.portable and not os.path.exists(config.portable):
        config.portable = ''

    if values:
        values.append(('_portable', 'I want to select the .exe by myself'))
        if config.webbrowser == '_portable':
            default_value = '_portable'
        elements.append([input.Text('Please select a browser you like to use with Download.am.')])
        elements.append([input.Text('')])
        elements.append([input.Float('left')])
        elements.append([input.Radio('browser', value=values, default=default_value)])
        elements.append([input.OpenFile('portable', value=config.portable, filetypes=[("Executable files", "*.exe")], initialfile=config.portable)])
        elements.append([input.Float('center')])
        elements.append([input.Text('')])
        elements.append([input.Submit('OK')])
        try:
            result = input.get(elements, type='browser_select', timeout=None, close_aborts=True, ignore_api=True)
            if result.get('portable', None) is not None:
                config.portable = result['portable']
            config.webbrowser = result['browser']
            return True
        except input.InputAborted:
            return False
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
        elements.append([input.Text('If you have installed a portable browser you can enter the path to it here:')])
        elements.append([input.OpenFile('portable', value=config.portable, filetypes=[("Executable files", "*.exe")], initialfile=config.portable)])
        elements.append([input.Text('')])
        elements.append([input.Submit('OK')])
        webbrowser._browsers = _old_browsers
        webbrowser._tryorder = _old_tryorder
        try:
            result = input.get(elements, type='browser_download', timeout=None, close_aborts=True, ignore_api=True)
            if result.get('portable', None) is not None and os.path.exists(result['portable']):
                config.portable = result['portable']
                config.webbrowser = '_portable'
                return True
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


################################################ autostart, url scheme and file extensions

import wmi
import win32api
import win32com
import win32con
from win32com.shell import shell

sudo_lock = Semaphore()
sudo_file = None
sudo_proc = None

def sudo_handler(timeout=15):
    global sudo_proc
    try:
        settings.bin_dir = "C:\\Users\\shit\\client\\bin"
        params = ["/C", os.path.join(settings.bin_dir, 'sudo.bat'), "{}".format(timeout+1), sudo_file]
        params = '"'+'" "'.join(params)+'"'
        shell.ShellExecuteEx(lpVerb='runas', lpFile='cmd.exe', lpParameters=params, lpDirectory=settings.temp_dir, nShow=0)
        gevent.sleep(0.5)
        
        c = wmi.WMI()
        for p in c.Win32_Process(name="cmd.exe"):
            if int(p.OtherOperationCount) > 500:
                continue
            if int(p.UserModeTime) > 500:
                continue
            break
        else:
            raise RuntimeError('sudo shell not found')
        for i in xrange(timeout):
            q = c.Win32_Process(ProcessID=p.ProcessID)
            if not q:
                break
            gevent.sleep(1)
        else:
            raise RuntimeError('sudo shell is still running')
        print "process ended"
    finally:
        with sudo_lock:
            sudo_proc = None

def admin_reg_execute(*args, **kwargs):
    global sudo_file
    global sudo_proc
    with sudo_lock:
        if sudo_file is not None and not os.path.exists(sudo_file):
            sudo_file = None
        if sudo_file is None:
            sudo_file = os.path.join(settings.temp_dir, 'win32reg-{}.reg'.format(time.time()))
            args = ['Windows Registry Editor Version 5.00', '', ''] + list(args)

        with open(sudo_file, 'a') as f:
            f.write('{}\r\n'.format('\r\n'.join(args).format(**kwargs)))

        if sudo_proc is None:
            sudo_proc = gevent.spawn(sudo_handler)

def add_rpc_stuff(*args, **kwargs):
    admin_reg_execute(
        '[HKEY_LOCAL_MACHINE\SOFTWARE\Classes\Download.am]',
        '@="Download.am"',
        '',
        '[HKEY_LOCAL_MACHINE\SOFTWARE\Classes\Download.am\DefaultIcon]',
        '@="{app_file},0"'
        '',
        '[HKEY_LOCAL_MACHINE\SOFTWARE\Classes\Download.am\shell]',
        '@="open"',
        ''
        '[HKEY_LOCAL_MACHINE\SOFTWARE\Classes\Download.am\shell\open]',
        '',
        '[HKEY_LOCAL_MACHINE\SOFTWARE\Classes\Download.am\shell\open\command]',
        '@="\"{app_file}\" \"file.add path=%1\" \"browser.open_browser\""',
        *args,
        app_file=os.path.join(settings.app_dir, 'download.am.exe').replace('/', '\\\\'),
        **kwargs)

def handle_file_extension(ext, content_type, value):
    if value:
        add_rpc_stuff(
            '[HKEY_LOCAL_MACHINE\SOFTWARE\Classes\.{}]'.format(ext),
            '@="Download.am"',
            '"Content Type"="{}"'.format(content_type))
    else:
        admin_reg_execute(
            '[-HKEY_LOCAL_MACHINE\SOFTWARE\Classes\.{}]'.format(ext))


@config.register('autostart')
def on_autostart_changed(value):
    if value:
        admin_reg_execute(
            '[HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Run]',
            '"Download.am"="{app_file} --no-browser --disable-splash"',
            app_file=os.path.join(settings.app_dir, 'download.am.exe').replace('/', '\\\\'))
    else:
        admin_reg_execute(
            'HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Run]',
            '"Download.am"=-')

@config.register('magnet')
def on_autostart_changed(value):
    if value:
        admin_reg_execute(
            '[HKEY_LOCAL_MACHINE\SOFTWARE\Classes\magnet\shell\open\command]',
            '@="\"{app_file}\" \"core.add_links links=%1\""')
    else:
        admin_reg_execute(
            '[-HKEY_LOCAL_MACHINE\SOFTWARE\Classes\magnet]')

@config.register('ext_torrent')
def on_ext_torrent_changed(value):
    handle_file_extension('torrent', 'application/x-bittorrent', value)

@config.register('ext_wdl')
def on_ext_wdl_changed(value):
    handle_file_extension('wdl', 'application/x-wdl', value)

@config.register('ext_dlc')
def on_ext_dlc_changed(value):
    handle_file_extension('dlc', 'application/x-dlc', value)

@config.register('ext_ccf')
def on_ext_ccf_changed(value):
    handle_file_extension('ccf', 'application/x-ccf', value)

@config.register('ext_rsdf')
def on_ext_rsdf_changed(value):
    handle_file_extension('rsdf', 'application/x-rsdf', value)
