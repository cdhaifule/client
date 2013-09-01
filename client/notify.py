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

from . import event, logger
from .api import proto

log = logger.get('notify')

def _send(type, message, seconds=None, local=True, remote=True):
    payload = dict(type=type, message=message, seconds=seconds)
    getattr(log, type)(message)
    if local:
        event.fire('notify', payload)
    if remote:
        proto.send('frontend', 'notify', payload=payload)

def info(message, seconds=None, local=True, remote=True):
    _send('info', message, seconds, local, remote)

def warning(message, seconds=None, local=True, remote=True):
    _send('warning', message, seconds, local, remote)

def error(message, seconds=None, local=True, remote=True):
    _send('error', message, seconds, local, remote)
