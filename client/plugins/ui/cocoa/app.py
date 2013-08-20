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
import signal
import atexit
import os

from gevent import event
import gevent

from AppKit import NSMenuItem, NSMenu, NSStatusBar, NSImage, NSSize, NSApplication, NSWindow, NSObject, NSTimer, NSDate, NSRunLoop

from PyObjCTools import AppHelper

from .... import settings, patch, login, localize
from ..systray.common import relogin, open_browser
from .window import Window

NSDefaultRunLoopMode = u'kCFRunLoopDefaultMode'

timerevent = event.Event()
class Delegate(NSObject):
    def restart_(self, noti):
        gevent.spawn(patch.restart_app)
        
    def open_(self, notification):
        open_browser()
        
    def logout_(self, notification):
        relogin()
    
    def test_(self, noti):
        relogin()
        
    def quit_(self, notification):
        exit(0)
        
    def gevent_(self, notification):
        gevent.sleep(0.1)
        
def mac_sigint(*args):
    exit(0)

def draw_browser(wind, deleg):
    frame = ((0.0, 0.0), (400, 10))
    return Window.web(wind, "Browser", frame, deleg)

browserwindow = None
    
NSVariableStatusItemLength = -1
def start_taskbar():
    t = NSStatusBar.systemStatusBar()
    icon = t.statusItemWithLength_(NSVariableStatusItemLength)
    icon.setHighlightMode_(1)
    menuitems = []
    labels = ["Open", "Logout", "Quit"]
    if patch.current.current == "DEV" or patch.config.branch == "master":
        labels = ["Open", "Logout", "Restart", "Test", "Quit"]
    for label in labels:
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            localize._X(label), label.lower().replace(" ", "").split(" ")[0]+":", "")
            
        iconpath = os.path.join(settings.menuiconfolder, label.lower() + ".icns")
        if os.path.exists(iconpath):
            img = NSImage.alloc().initByReferencingFile_(iconpath)
            img.setSize_(NSSize(16, 16))
            item.setImage_(img)
        menuitems.append(item)
    
    menu = NSMenu.alloc().init()
    menu.setAutoenablesItems_(True)
    icon.setMenu_(menu)
    for m in menuitems:
        menu.addItem_(m)
    taskbarimg = NSImage.alloc().initByReferencingFile_(settings.taskbaricon)
    taskbarimg.setSize_(NSSize(18, 18))
    icon.setImage_(taskbarimg)
    icon.setEnabled_(True)
    
    @login.config.register('username')
    def _():
        item = menu.itemAtIndex_(0)
        item.setTitle_(login.config.username or "Open")

def gevent_timer(deleg):
    timer = NSTimer.alloc().initWithFireDate_interval_target_selector_userInfo_repeats_(
                NSDate.date(), 0.1, deleg, 'gevent:', None, True)
    NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSDefaultRunLoopMode)
    timer.fire()
    print "started gevent timer"

_exited = False
def exit(code=0):
    global _exited
    if _exited:
        return
    AppHelper.stopEventLoop()
    from .... import loader
    loader.terminate()
    _exited = True
    
def init():
    global browserwindow
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(1)
    start_taskbar()
    app.finishLaunching()
    _browserwindow = NSWindow.alloc()
    icon = NSImage.alloc().initByReferencingFile_(settings.mainicon)
    app.setApplicationIconImage_(icon)
    
    deleg = Delegate.alloc()
    deleg.init()
    app.setDelegate_(deleg)
    
    signal.signal(signal.SIGINT, mac_sigint)
    
    from .input import BrowserDelegate
    bd = BrowserDelegate.alloc()
    bd.init()
    browserwindow = draw_browser(_browserwindow, bd)
    from .... import ui
    ui.log.debug('using cocoa')
    atexit.register(exit)
    gevent_timer(deleg)
    ui.module_initialized.set()
    sys.exit = exit
    AppHelper.runEventLoop()
