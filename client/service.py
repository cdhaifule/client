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

from . import event, interface, logger, plugintools
from .config import globalconfig

config = globalconfig.new("service")
log = logger.get('servicemanager')

class ServiceManager(dict):
    def add(self, plugin):
        self[plugin.name] = plugin
        event.fire('service:new', plugin)

class ServicePlugin(plugintools.GreenletObject):
    default_enabled = True
    
    def __init__(self, name):
        plugintools.GreenletObject.__init__(self)
        
        self.name = name
        self.greenlet = None
        self.log = logger.get('service').getLogger(name)
        self.config = config.new(self.name)
        self.config.default('enabled', self.default_enabled, bool)
        
        @self.config.register("enabled")
        def start_stop(value):
            if value:
                self.start()
            else:
                self._stop()
        
        start_stop(self.config.enabled)

    def start(self):
        self.config['enabled'] = True
        if not self.greenlet:
            self.spawn(self._run)
            self.log.info("started")
            event.fire('service:started', self)

    def _stop(self):
        self.config['enabled'] = False
        if self.greenlet:
            self.stop()
            self.greenlet.kill()
            self.log.info("stopped")
            event.fire('service:stopped', self)

    @property
    def running(self):
        return self.greenlet and True or False

    def _run(self):
        try:
            self.run()
        finally:
            gevent.spawn(self._stop)

    def run(self):
        raise NotImplementedError()
        
    def stop(self):
        return

@interface.register
class AccountInterface(interface.Interface):
    name = "service"
    
    def list_plugins():
        """lists all service plugins"""
        return [{"name": plugin.name, "running": plugin.running} for plugin in manager.values()]

manager = ServiceManager()
register = manager.add

def init():
    plugintools.load('service')

def terminate():
    for plugin in manager.values():
        if plugin.running:
            log.info("\tstop running {}".format(plugin.name))
            plugin.stop()
        else:
            log.info("\t{} is not running".format(plugin.name))