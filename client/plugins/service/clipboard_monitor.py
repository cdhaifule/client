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

import re
import os
import sys
import gevent

from bs4 import BeautifulSoup

from ... import logger, service, core, hoster, interface

def extracthtml(html):
    html = BeautifulSoup(html)
    links = set()
    for a in html.findAll("a"):
        try:
            links.add(a["href"])
        except KeyError:
            pass
            
    for img in html.select("img"):
        try:
            links.add(img["src"])
        except KeyError:
            pass
    return links

def tkpaste():
    if not "DISPLAY" in os.environ:
        return 0
    try:
        from Tkinter import Tk
    except ImportError:
        return 1
    _tk = Tk()
    _tk.withdraw()

    def paste():
        try:
            return _tk.selection_get(selection="CLIPBOARD")
        except BaseException as e:
            if str(e) != """CLIPBOARD selection doesn't exist or form "STRING" not defined""":
                log.debug('tk paste error: {}'.format(e))
            return ""
    return paste
    
def macpaste():
    from AppKit import NSPasteboard
    pb = NSPasteboard.generalPasteboard()
    lastitems = [None]

    def paste():
        items = pb.pasteboardItems()
        if lastitems[0] == items:
            return False # if the clipboard did not change, return False, more performance
        else:
            lastitems[0] = items
            
        links = set()
        for item in items:
            types = set(item.types())
            if "public.html" in types:
                links.update(extracthtml(item.dataForType_("public.html").bytes().tobytes()))
            if "public.url" in types:
                links.add(item.stringForType_("public.url"))
            if "public.rtf" in types:
                # find HYPERLINK, used especially by safari and adium
                links.update(re.findall(r'HYPERLINK "(.*?)"', item.stringForType_("public.rtf")))
            for t in types:
                m = re.match("public.(.*?)-plain-text", t)
                if m:
                    try:
                        encoding = m.group(1)
                        f = encoding.find("-external")
                        if f>0:
                            encoding = encoding[:f]
                        data = item.dataForType_(t).bytes().tobytes().decode(encoding)
                    except LookupError:
                        continue
                    if data:
                        links |= hoster.collect_links(data)
                    break # do not parse multiple encodings
        return links
    return paste

try:
    import win32clipboard as w
    
    def paste():
        try:
            w.OpenClipboard()
            try:
                data = w.GetClipboardData(w.GetPriorityClipboardFormat([49358, w.CF_UNICODETEXT, w.CF_TEXT, w.CF_OEMTEXT]))
            except TypeError as e:
                if str(e) != 'Specified clipboard format is not available':
                    log.debug("typerror windows clipboard: {}".format(e))
                return ""
            else:
                f = data.find("<!--StartFragment-->") # html fragment data from firefox or chrome
                if f >= 0:
                    return extracthtml(data[f:])
                else:
                    return data
        finally:
            w.CloseClipboard()
except ImportError:
    w = False
    if sys.platform == "darwin":
        paste = macpaste()
    else:
        if "DISPLAY" in os.environ:
            paste = tkpaste()
        else:
            paste = None

log = logger.get('clipboard_monitor')

def read_links(data):
    if isinstance(data, list):
        return data
    elif isinstance(data, set):
        return list(data)
    else:
        if data:
            return list(hoster.collect_links(data))
        return list()

class ClipboardMonitor(service.ServicePlugin):
    default_enabled = False
    def __init__(self, name):
        service.ServicePlugin.__init__(self, name)
        self.config.default("ignore_plugins", list(), list, description="List of ignored plugins for clipboard monitor.")
    
    def run(self):
        if not callable(paste):
            self.stop()
            return
        current = paste()
        while self.greenlet:
            gevent.sleep(1)
            new = paste()
            if not new:
                continue
            if new != current:
                self.log.debug("clipboard changed")
                current = new
                links = read_links(current)
                if links:
                    core.add_links(links, ignore_plugins=['http', 'ftp'])
        self.log.info("stopped")

cpmonitor = ClipboardMonitor('clipboard_monitor')
service.register(cpmonitor)

@interface.register
class Interface(interface.Interface):
    name = 'service.clipboard_monitor'

    def get_links():
        if paste is not None:
            links = set()
            try:
                data = paste()
                links = read_links(data)
            finally:
                return list(links)

    def read_clipboard(ignore_plugins=[]):
        if paste is not None:
            data = paste()
            links = read_links(data)
            if links:
                core.add_links(links, ignore_plugins=ignore_plugins or cpmonitor.config.ignore_plugins)
