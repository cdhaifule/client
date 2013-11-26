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

config.default('autostart', True, bool, protected=True, persistent=False)
config.default('ext_torrent', True, bool, protected=True, persistent=False)
config.default('ext_wdl', True, bool, protected=True, persistent=False)
config.default('ext_dlc', True, bool, protected=True, persistent=False)
config.default('ext_ccf', True, bool, protected=True, persistent=False)
config.default('ext_rsdf', True, bool, protected=True, persistent=False)
config.default('scheme_magnet', True, bool, protected=True, persistent=False)


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
        try:
            enum = list(enum_reg_keys(hkey, 'Software\\Clients\\StartMenuInternet'))
        except WindowsError:
            # key not exists or something?
            continue
        for key in enum:
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
                try:
                    version = int(read_reg_key(hkey, 'Software\\Microsoft\\Internet Explorer', 'Version')[0].split('.', 1)[0])
                except AttributeError: # this maybe happens, don't know why. assume IE is outdated
                    version = 0
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

class Sudo(object):
    def __init__(self):
        self.lock = Semaphore()
        self.file = None
        self.timeout = None

    def reg_execute(self, args):
        if not args:
            return
        with self.lock:
            if self.file is not None and os.path.exists(self.file+'.reg'):
                for _ in xrange(3):
                    gevent.sleep(1)
                    if not os.path.exists(self.file+'.reg'):
                        break
                else:
                    print "WARNING: file stll exists after 2 seconds. assuming sudo process is dead"
                    self.end()

            if self.file is None:
                self.file = os.path.join(settings.temp_dir, 'win32reg-{}'.format(time.time()))

            with open(self.file+'.reg.tmp', 'a') as f:
                f.write('Windows Registry Editor Version 5.00\r\n\r\n\r\n')
                f.write('{}\r\n'.format('\r\n'.join(args)))
            os.rename(self.file+'.reg.tmp', self.file+'.reg')

            if self.timeout is None:
                params = [os.path.join(settings.bin_dir, 'sudo.bat'), self.file+'.reg', self.file+'.end']
                params = '/C ""'+'" "'.join(params)+'""'
                shell.ShellExecuteEx(lpVerb='runas', lpFile='cmd.exe', lpParameters=params, lpDirectory=settings.temp_dir, nShow=0)
            elif self.timeout is not None:
                self.timeout.kill()
            self.timeout = gevent.spawn_later(30, self._timeout_handler)

    def end(self):
        try:
            with open(self.file+'.end.tmp', 'a') as f:
                f.write('end')
            os.rename(self.file+'.end.tmp', self.file+'.end')
        finally:
            gevent.spawn_later(60, self._cleanup, self.file)
            self.file = None
            self.timeout = None

    def _timeout_handler(self):
        with self.lock:
            self.end()

    def _cleanup(self, file):
        for p in ('.reg', '.reg.tmp', '.end', '.end.tmp'):
            try:
                os.unlink(file+p)
            except:
                pass

sudo = Sudo()


hkey_to_str = {
    HKEY_CLASSES_ROOT: 'HKEY_CLASSES_ROOT',
    HKEY_LOCAL_MACHINE: 'HKEY_LOCAL_MACHINE',
    HKEY_CURRENT_USER: 'HKEY_CURRENT_USER'}

def add_sudo_reg(hkey, key, key_value=None, subkey=None, subkey_value=None):
    args = list()
    if key_value is not None:
        value = read_reg_key(hkey, key)[0]
        if value != key_value:
            args.append(u'@="{}"'.format(key_value.replace('\\', '\\\\').replace('"', '\\"')))
    if subkey is not None:
        if subkey_value is None:
            raise RuntimeError('subkey_value can not be none')
        value = read_reg_key(hkey, key, subkey)[0]
        if value != subkey_value:
            args.append(u'"{}"="{}"'.format(subkey.replace('"', '\\"'), subkey_value.replace('\\', '\\\\').replace('"', '\\"')))
    if args:
        args.insert(0, u'[{}\\{}]'.format(hkey_to_str[hkey], key))
        args.append('')
    return args

def del_sudo_reg(hkey, key, subkey=None, expected_value=None):
    args = list()
    value = read_reg_key(hkey, key, '' if subkey is None else subkey)[0]
    if value is not None and (expected_value is None or value == expected_value):
        if subkey is None:
            args.append(u'[-{}\\{}]'.format(hkey_to_str[hkey], key))
        else:
            args.append(u'[{}\\{}]'.format(hkey_to_str[hkey], key))
            args.append(u'"{}"=-'.format(subkey.replace('"', '\\"')))
    return args

def on_autostart_changed(add, execute=True):
    if add:
        app_file = os.path.join(settings.app_dir, 'download.am.exe')
        args = add_sudo_reg(HKEY_LOCAL_MACHINE, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Run', None, 'Download.am', '{} --no-browser --disable-splash'.format(app_file))
    else:
        args = del_sudo_reg(HKEY_LOCAL_MACHINE, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Run', 'Download.am')
    if execute and args:
        sudo.reg_execute(args)
    return args

@config.register('scheme_magnet')
def on_scheme_magnet_changed(add, execute=True):
    if add:
        app_file = os.path.join(settings.app_dir, 'download.am.exe')
        args = add_sudo_reg(HKEY_LOCAL_MACHINE, 'SOFTWARE\Classes\magnet', 'URL:magnet protocol', 'URL Protocol', '')
        args += add_sudo_reg(HKEY_LOCAL_MACHINE, 'SOFTWARE\Classes\magnet\shell\open\command', '"{}" "core.add_links links=%1"'.format(app_file))
    else:
        args = del_sudo_reg(HKEY_LOCAL_MACHINE, 'SOFTWARE\Classes\magnet')
    if execute and args:
        sudo.reg_execute(args)
    return args

def handle_file_extension(ext, content_type, add, execute=True):
    if add:
        args = add_sudo_reg(HKEY_LOCAL_MACHINE, 'SOFTWARE\Classes\.{}'.format(ext), 'Download.am File', 'Content Type', content_type)
        if args:
            app_file = os.path.join(settings.app_dir, 'download.am.exe')
            args += add_sudo_reg(HKEY_LOCAL_MACHINE, 'SOFTWARE\Classes\Download.am File', 'Download.am File')
            args += add_sudo_reg(HKEY_LOCAL_MACHINE, 'SOFTWARE\Classes\Download.am File\DefaultIcon', '{},0'.format(app_file))
            args += add_sudo_reg(HKEY_LOCAL_MACHINE, 'SOFTWARE\Classes\Download.am File\shell\open\command', '"{}" "file.add path=%1"'.format(app_file))
    else:
        args = del_sudo_reg(HKEY_LOCAL_MACHINE, 'SOFTWARE\Classes\.{}'.format(ext))
    if execute and args:
        sudo.reg_execute(args)
    return args

def on_ext_torrent_changed(add, execute=True):
    return handle_file_extension('torrent', 'application/x-bittorrent', add, execute)

def on_ext_wdl_changed(add, execute=True):
    return handle_file_extension('wdl', 'application/x-wdl', add, execute)

def on_ext_dlc_changed(add, execute=True):
    return handle_file_extension('dlc', 'application/x-dlc', add, execute)

def on_ext_ccf_changed(add, execute=True):
    return handle_file_extension('ccf', 'application/x-ccf', add, execute)

def on_ext_rsdf_changed(add, execute=True):
    return handle_file_extension('rsdf', 'application/x-rsdf', add, execute)

def init():
    config.autostart = False if on_autostart_changed(True, False) else True
    config.scheme_magnet = False if on_scheme_magnet_changed(True, False) else True
    config.ext_torrent = False if on_ext_torrent_changed(True, False) else True
    config.ext_wdl = False if on_ext_wdl_changed(True, False) else True
    config.ext_dlc = False if on_ext_dlc_changed(True, False) else True
    config.ext_ccf = False if on_ext_ccf_changed(True, False) else True
    config.ext_rsdf = False if on_ext_rsdf_changed(True, False) else True

    config.register_hook('autostart', lambda add: on_autostart_changed(add, True))
    config.register_hook('scheme_magnet', lambda add: on_scheme_magnet_changed(add, True))
    config.register_hook('ext_torrent', lambda add: on_ext_torrent_changed(add, True))
    config.register_hook('ext_wdl', lambda add: on_ext_wdl_changed(add, True))
    config.register_hook('ext_dlc', lambda add: on_ext_dlc_changed(add, True))
    config.register_hook('ext_ccf', lambda add: on_ext_ccf_changed(add, True))
    config.register_hook('ext_rsdf', lambda add: on_ext_rsdf_changed(add, True))

# TODO: remove this block when update propagation is done

@event.register('config:before_load')
def on_config_before_load(e, data):
    for key in ('autostart', 'ext_torrent', 'ext_wdl', 'ext_dlc', 'ext_ccf', 'ext_rsdf', 'scheme_magnet'):
        key = 'registry.win.{}'.format(key)
        data.pop(key, None)
