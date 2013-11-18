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

import gevent
import os
import requests

from ...reconnect import translate, config, log
from ...plugintools import between
from ... import interface, settings

reconnectscriptsdir = os.path.join(settings.bin_dir, "reconnect")

config.default("script_name", "", unicode, protected=True)

name = "script"


def is_configured():
    return config.script_name and True or False


def load(name):
    p = os.path.join(reconnectscriptsdir, name)
    try:
        with open(p, 'rb') as f:
            return f.read()
    except (OSError, IOError):
        log.exception(u"error opening reconnect script {}".format(name))


def reconnect():
    data = load(config["script_name"])
    if data is None:
        return False

    s = translate(data)
    session = requests.session()
    d = dict(get=session.get, post=session.post, session=session)
    try:
        exec s in d
    except KeyboardInterrupt:
        raise
    except:
        log.exception("error while executing reconnect script")
        return False
    else:
        return True


def save(name, data):
    if not name.endswith('-custom'):
        name = name + '-custom'
    p = os.path.join(reconnectscriptsdir, name)
    try:
        with open(p, "wb") as f:
            f.write(data.strip())
        config['script_name'] = name
    except (OSError, IOError):
        log.exception("error writing custom reconnect script {}".format(name))
        return False
    else:
        try:
            desc = between(data, '"""', '"""')
        except ValueError:
            desc = ""
        scripts[name] = desc
        return True


scripts = dict()


def index():
    for i, fn in enumerate(os.listdir(reconnectscriptsdir)):
        try:
            with open(os.path.join(reconnectscriptsdir, fn)) as f:
                data = f.read()
                try:
                    desc = between(data, '"""', '"""').strip()
                except ValueError:
                    desc = ""
        except (IOError, OSError):
            log.exception("unable to open reconnect script template "+fn)
        else:
            scripts[fn] = desc
        
        if i % 2 == 0:
            gevent.sleep()
index()


@interface.register
class ReconnectScript(interface.Interface):
    name = "reconnect.script"
    
    @interface.protected
    def save(name=None, data=None):
        return save(name, data)

    @interface.protected
    def set(name=None):
        config['script_name'] = name and name or ""
        return True
        
    def load(name=None):
        return dict(data=load(name))

    def list():
        return {n: d for n, d in scripts.iteritems()}
        
    def reindex():
        index()
