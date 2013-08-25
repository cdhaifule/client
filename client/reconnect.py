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

import time
import socket

import gevent
import requests

from . import plugintools, event, settings, logger, interface, core
from .config import globalconfig
from .input import Text, Choice, get as get_input, InputTimeout, InputError

log = logger.get("reconnect")

config = globalconfig.new("reconnect")
config.default("method", 'script', str)  # script|extern
config.default("auto", False, bool)
config.default("timeout", 60, int)
config.default("retry", 2, int)

@config.register('method')
def check_method(value):
    if value is None:
        config['auto'] = False
    elif value not in manager.methods:
        config['method'] = None
        log.error('unknown reconnect method: {}'.format(value))

@config.register('auto')
def check_auto(value):
    if value and config.method is None:
        config['auto'] = False
        log.error('cannot reonnect without a method')

def guess_router_ip(): # xxx better method?
    localip = socket.gethostbyname(socket.gethostname())
    return localip.rsplit(".", 1)[0] + ".1"

try:
    config.default("routerip", guess_router_ip(), str)
except:
    config.default("routerip", "", str)

config.default("username", "admin", str) # router login data
config.default("password", "", str)      # useful for all plugins

def get_extern_ip():
    resp = requests.get(settings.patchserver + "/ip")
    return resp.content

def translate(s, _notranslate={"auto", "method"}):
    for key in (config._defaults - _notranslate):
        try:
            s = s.replace("{"+key+"}", config[key])
        except TypeError:
            s = s.replace("{"+key+"}", repr(config[key]))
    return s
    
class Reconnect(object):
    def __init__(self):
        self.methods = dict()
        self.reconnecting = False
    
    def reconnect(self):
        """reconnect"""
        global reconnecting
        if self.reconnecting:
            return 'already_connecting'
        if config["method"] is None:
            return 'method_not_set'
        method = self.methods[config["method"]]
        if not method.is_configured():
            return 'method_not_set'
        reconnecting = self.reconnecting = True
        try:
            newip = None
            oldip = get_extern_ip()
            log.info('starting reconnect. current ip: {}'.format(oldip))
            event.fire('reconnect:reconnecting')
            for i in xrange(config["retry"]):
                try:
                    method.reconnect()
                except KeyboardInterrupt:
                    raise
                except:
                    log.exception("reconnect failed")
                    continue
                finally:
                    #event.fire('reconnect:reconnected')
                    pass
                    
                t = time.time() + config["timeout"]
                while time.time() < t:
                    gevent.sleep(1)
                    try:
                        newip = get_extern_ip()
                    except KeyboardInterrupt:
                        raise
                    except:
                        log.exception("getting ip, will try again.")
                        continue
                    else:
                        break
                if newip != oldip:
                    break
                if i < config['retry']:
                    log.debug('reconnect retry #{} failed'.format(i + 1))
            if newip != oldip and newip is not None:
                log.info('reconnect successful. new ip: {}'.format(newip))
                event.fire('reconnect:success')
            else:
                log.info('reconnect failed after {} retries'.format(i + 1))
                event.fire('reconnect:failed')
                try:
                    elements = [Text("Reconnect failed."), 
                        Choice('answer', choices=[
                            {"value": 'ignore', "content": "Ignore"},
                            {"value": 'stop', "content": "Deactivate Auto-Reconnect?"}
                            ])
                        ]
                    result = get_input(elements)["answer"]
                except KeyError:
                    result = 'stop'
                except InputTimeout:
                    log.warning('input timed out')
                    result = 'stop'
                except InputError:
                    log.error('input was aborted')
                    result = 'stop'
                    
                if result == "stop":
                    with core.transaction:
                        config.auto = False
            newip = get_extern_ip()
        finally:
            reconnecting = self.reconnecting = False
            event.fire('reconnect:done')

    def add(self, name, func):
        self.methods[name] = func
    
    def register(self, name):
        def reg(func):
            self.add(name, func)
            return func
        return reg
        
manager = Reconnect()
reconnect = manager.reconnect
reconnecting = False

def init():
    for mod in plugintools.load("reconnect"):
        manager.add(mod.name, mod)
    

@interface.register
class ReconnectScript(interface.Interface):
    name = "reconnect"
    
    def reconnect():
        return dict(error=reconnect())

    def list():
        return list(manager.methods)
