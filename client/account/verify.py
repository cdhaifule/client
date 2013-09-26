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

import time
import gevent
import requests
from gevent.event import AsyncResult
from ..api import proto

keys = dict()

def request_key(hoster):
    keys[hoster] = AsyncResult()
    proto.send("backend", command="api.getsecret", payload=dict(hoster=hoster))
    return _get(hoster)

def _get(hoster):
    try:
        return keys[hoster].get(timeout=8)
    except gevent.Timeout:
        print "timeout requesting secret for hoster", hoster
        return None, None

def get_key(hoster):
    if hoster in keys:
        value, expire = _get(hoster)
        print value, expire
        if expire < time.time()-30:
            value, expire = request_key(hoster)
    else:
        value, expire = request_key(hoster)
    return value

def set_secret(hoster, secret, timeleft):
    if hoster not in keys:
        keys[hoster] = AsyncResult()
    keys[hoster].set((secret, time.time()+timeleft))

def get_agent(hoster):
    default = requests.utils.default_user_agent()
    key = get_key(hoster)
    if key is None:
        return default
    else:
        return "{} (download.am; ID {})".format(default, get_key(hoster))
