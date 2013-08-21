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
from functools import partial
import gevent

from ....login import logout, get_sso_url
from .... import event

def relogin(*_):
    logout()
    
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
