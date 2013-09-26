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

import os

from functools import partial
from gevent import threadpool

import win32gui
import win32con
import glob
from PIL import IcnsImagePlugin, BmpImagePlugin # preload for py2exe
from PIL import Image
from gevent.event import Event
from gevent._threading import Lock as ThreadingLock

from .... import settings, event, login, localize, core
from ....contrib.systrayicon import SysTrayIcon

from . import common

_X = localize.X

def menu_color():
    return win32gui.GetSysColor(win32con.COLOR_MENU)
    
def create_icon(name, inputpath, width, height, bgcolor):
    path = os.path.join(settings.menuiconfolder, "{}_{}_{}_{}.bmp".format(name, width, height, bgcolor))
    if os.path.exists(path):
        return path
    source = Image.open(inputpath)
    source.thumbnail((width, height), Image.ANTIALIAS)
    r, g, b, alpha = source.split()
    new = Image.new(source.mode, source.size, bgcolor)
    new.paste(source, mask=alpha)
    new.convert("RGB").save(path, "BMP")
    return path
    
def bmp_factory(name, path=None):
    if path is None:
        path = os.path.join(settings.menuiconfolder, '{}.icns'.format(name))
        if not os.path.exists(path):
            raise RuntimeError("Loading of icon for action '{}' failed".format(name))
    return partial(create_icon, name, path)


class SysTray(SysTrayIcon):
    def __init__(self, *args, **kwargs):
        event.add('api:connected', self._set_active_icon)
        event.add("api:disconnected", self._set_inactive_icon)
        event.add("api:connection_error", self._set_inactive_icon)
        SysTrayIcon.__init__(self, *args, **kwargs)
        
    def _set_active_icon(self, *_):
        self.icon = settings.taskbaricon
        self.refresh_icon()
        
    def _set_inactive_icon(self, *_):
        self.icon = settings.taskbaricon_inactive
        self.refresh_icon()

def init():
    lock = ThreadingLock()
    init_event = Event()

    icons = glob.glob(os.path.join(settings.menuiconfolder, "*.icns"))
    for i in icons:
        name = os.path.basename(i)
        name = name[:name.rfind(".")]
    
    thread = threadpool.ThreadPool(1)

    _open = (_X("Open"), bmp_factory('open'), lambda *_: event.call_from_thread(common.open_browser))
    _browser = (_X("Select browser"), bmp_factory('browser'), lambda *_: event.call_from_thread(common.select_browser))
    _register = (_X("Register"), bmp_factory('register'), lambda *_: event.call_from_thread(common.register))
    _login = (_X("Login"), bmp_factory('login'), lambda *_: event.call_from_thread(common.relogin))
    _logout = (_X("Logout"), bmp_factory('logout'), lambda *_: event.call_from_thread(common.relogin))
    _quit = (_X("Quit"), bmp_factory('quit'), 'QUIT')
    options = [_open, _browser, _login, _register, _quit]

    icon = settings.taskbaricon_inactive
    if not icon:
        return

    def update_tooltip():
        text = common.generate_tooltip_text()

        if SysTray.instance.tooltip_text != text:
            with lock:
                SysTray.instance.tooltip_text = text
                SysTray.instance.refresh_icon()

    thread.spawn(SysTray, icon, options, lambda *_: event.call_from_thread(common.quit), 0, "download.am",
                 lock=lock,
                 init_callback=lambda _: event.call_from_thread(init_event.set),
                 update_tooltip_callback=update_tooltip)
    init_event.wait()

    @event.register('login:changed')
    def on_login_changed(*_):
        opts = list()
        opts.append(_open)
        opts.append(_browser)
        if login.is_guest() or not login.has_login():
            opts.append(_login)
            opts.append(_register)
        elif login.has_login():
            opts.append(_logout)
        else:
            opts.append(_register)
        opts.append(_quit)

        if opts != options:
            with lock:
                options[:] = opts
                SysTray.instance.init_menu_options(options)

    @event.register('loader:initiialized')
    @core.GlobalStatus.files.changed
    @core.GlobalStatus.files_working.changed
    def on_update_tooltip(*_):
        event.fire_once_later(1, 'systray.win:update_tooltip')

    try:
        on_login_changed()
    except:
        pass
