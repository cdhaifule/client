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

from . import monkey
monkey.init()

import traceback
import re
import sys
import signal
import gevent
import optparse

ooo = gevent.threading._start_new_thread
def foo(*args, **kwargs):
    print args, kwargs
    return ooo(*args, **kwargs)
gevent.threading._start_new_thread = foo

from gevent.event import Event
from importlib import import_module
from types import GeneratorType

pre_objects = ['settings', 'event', 'logger', 'localize', 'config', 'db', 'interface', 'localrpc']
main_objects = ['ui']
post_objects = ['proxy', 'patch', 'login', 'api', 'ratelimit', 'reconnect', 'hoster', 'account', 'fileplugin', 'core', 'check', 'torrent', 'download', 'service', 'browser']

ui = None
log = None
event = None
localize = None

modules_by_name = dict()

def main_loop():
    ui.main_loop()

def init():
    try:
        global ui
        global log
        global event
        global localize
        global main_loop

        # call init_pre
        _init_pre(pre_objects)
        _init_pre(main_objects)
        _init_pre(post_objects)

        ui = modules_by_name['ui']
        event = modules_by_name['event']
        localize = modules_by_name['localize']

        log = modules_by_name['logger'].get('loader')

        # register terminate handler
        if sys.platform != 'darwin':
            gevent.signal(signal.SIGINT, terminate)
            gevent.signal(signal.SIGTERM, terminate)
            #atexit.register(terminate)

        # initialize optparser (init_optparser)
        options, args = _init_optparser()

        # pre init complete, fire initializing event
        event.fire('loader:initializing')

        # main initialisation
        _init(pre_objects, options, args)
        g = gevent.spawn(_init, post_objects, options, args)
        _init(main_objects, options, args) # this call MAY block (it does on MACOS)

        # wait until post object load is complete
        g.join()
    except (KeyboardInterrupt, SystemExit):
        raise
    except:
        def log_exception(msg):
            if log:
                log.exception(msg)
            else:
                print msg
                traceback.print_exc()

        log_exception('trying recovery patch')

        # load patch module
        if 'patch' not in modules_by_name:
            from . import patch
            modules_by_name['patch'] = patch
        else:
            patch = modules_by_name['patch']

        # initialize patch module
        if not patch.module_initialized.is_set():
            try:
                for msg in patch.init():
                    msg = 'initializing patch: {}'.format(msg)
                    if log:
                        log.info(msg)
                    else:
                        print msg
            except:
                log_exception('error initializing patch system')
        else:
            # apply patches
            try:
                patch.patch_all()
            except:
                log_exception('error running patch all')

        # reraise exception
        raise

def _init_pre(modules):
    for i, module in enumerate(modules):
        if _terminated:
            sys.exit(0)
        obj = modules[i] = import_module('.'+module, 'client')
        modules_by_name[module] = obj
        globals()[module] = obj
        
        obj.module_initialized = Event()
        if hasattr(obj, 'init_pre') and callable(obj.init_pre):
            obj.init_pre()

def _init_optparser():
    parser = optparse.OptionParser(add_help_option=False, usage=localize.T.loader__usage)
    
    def format_epilog(self):
        return self.parser.epilog and self.parser.epilog or self.parser.epilog
    parser.format_epilog = format_epilog

    parser.add_option('-h', '--help', action='help', help=localize.T.loader__help)

    for obj in pre_objects + main_objects + post_objects:
        if _terminated:
            sys.exit(0)
        if hasattr(obj, 'init_optparser'):
            if obj.init_optparser.func_code.co_argcount == 2:
                g = obj.init_optparser(parser, optparse.OptionGroup)
                if isinstance(g, optparse.OptionGroup):
                    parser.add_option_group(g)
            else:
                obj.init_optparser(parser)

    return parser.parse_args()

def _init(objects, options, args):
    for obj in objects:
        if _terminated:
            sys.exit(0)
        name = re.sub(r'^client\.', '', obj.__name__)
        text = 'initializing {}'.format(name)
        ui.set_splash_text(text)
        log.debug(text)
        if not hasattr(obj, 'init'):
            continue
        try:
            if obj.init.func_code.co_argcount == 1:
                result = obj.init(options)
            elif obj.init.func_code.co_argcount == 2:
                result = obj.init(options, args)
            else:
                result = obj.init()
            if isinstance(result, GeneratorType):
                for t in result:
                    if _terminated:
                        sys.exit(0)
                    t = '{}: {}'.format(text, t)
                    ui.set_splash_text(t)
                    log.debug(t)
            obj.module_initialized.set()
        except (KeyboardInterrupt, SystemExit):
            raise
        except:
            log.critical('failed initializing {}'.format(name), exc_info=sys.exc_info)
            raise
    if objects == post_objects:
        event.fire('loader:initialized')

_terminated = False

def terminate():
    global _terminated
    if _terminated:
        return
    _terminated = True
    gevent.signal(signal.SIGINT, None)
    gevent.signal(signal.SIGTERM, None)
    if ui:
        ui.set_splash_text('terminating...')
    if event:
        event.fire('loader:terminating')
    for obj in reversed(pre_objects + main_objects + post_objects):
        if hasattr(obj, 'terminate'):
            name = re.sub(r'^client\.', '', obj.__name__)
            log.debug('terminating module {}'.format(name))
            try:
                obj.terminate()
            except (AssertionError, SystemExit, KeyboardInterrupt, gevent.GreenletExit):
                print "exit error catched in", name
                pass
            except:
                traceback.print_exc()
                log.critical('failed terminating module {}'.format(name), exc_info=sys.exc_info)
    sys.exit(0)
