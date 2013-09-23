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
import webbrowser
import gevent

from functools import partial

from ....login import logout, get_sso_url
from .... import event, settings
from .... import settings, event, login, localize, core, download, torrent
from ....speedregister import globalspeed
from ....contrib.sizetools import bytes2human

def relogin(*_):
    logout()

def select_browser(*_):
    event.fire('registry:select_browser', True)
    
def register(*_):
    webbrowser.open_new_tab('https://{}/#register'.format(settings.frontend_domain))
    
def _open_browser(*_):
    webbrowser.open_new_tab(get_sso_url())

@event.register("api:connected")
def set_open_browser(*_):
    global open_browser
    open_browser = _open_browser

@event.register("api:disconnected")
@event.register("api:connection_error")
def set_connection_error(*_):
    global open_browser
    from ....api import client
    open_browser = partial(gevent.spawn, client.connection_error)

open_browser = _open_browser
    
def quit(*_):
    sys.exit(0)

def generate_tooltip_text():
    if download.config.state == 'stopped' and torrent.config.state == 'stopped':
        return localize.T.systray__win__tooltip_stopped
    else:
        files_queued = 0
        bytes_complete, bytes_total = 0, 0
        for p in core.packages():
            if p.tab not in ('collect', 'complete'):
                bytes_total += p.size
                bytes_complete += p.size*(p.progress or 0)
                files_queued += len([f for f in p.files if f.enabled])
        if files_queued:
            template = localize.T.systray__win__tooltip
        else:
            template = localize.T.systray__win__tooltip_idle

        return template.format(
            complete=bytes2human(bytes_complete),
            total=bytes2human(bytes_total),
            working=core.global_status.files_working,
            queued=files_queued,
            speed=bytes2human(globalspeed.get_bytes()))
