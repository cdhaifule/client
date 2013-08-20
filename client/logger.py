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
import sys
import json
import gevent
import atexit
import hashlib
import traceback

import logging
import logging.handlers

from . import settings

# logger class

sent_script_errors = []
logging.raiseExceptions = False
handlers = []
loggerClass = logging.getLoggerClass()

class MyLogger(loggerClass):
    def __init__(self, *args, **kwargs):
        loggerClass.__init__(self, *args, **kwargs)

    def getLogger(self, name):
        return logging.getLogger(u"%s.%s" % (self.name, name))

    def debug(self, *args, **kwargs):
        return loggerClass.debug(self, *args, **kwargs)

    def info(self, *args, **kwargs):
        return loggerClass.info(self, *args, **kwargs)

    def warning(self, *args, **kwargs):
        return loggerClass.warning(self, *args, **kwargs)

    def error(self, *args, **kwargs):
        return loggerClass.error(self, *args, **kwargs)

    def critical(self, *args, **kwargs):
        return loggerClass.critical(self, *args, **kwargs)

    def exception(self, *args, **kwargs):
        return loggerClass.exception(self, *args, **kwargs)

    def unhandled_exception(self, message, *args, **kwargs):
        if 'exc_info' not in kwargs:
            kwargs['exc_info'] = sys.exc_info()
        gevent.spawn(self.send, 'exception', message, kwargs['exc_info'])
        self.exception(message, *args)

    def send(self, type, message, exc_info=None):
        """`type` is exception, critical, warning, unhandled_exception ...
        `message` is a short description
        `exc_info` is an exc_info object or a report
        """
        from . import patch
        
        log_targets = list()

        message = unicode(message)
        if re.match(r'^<Greenlet at .* failed with [^\s]+$', message):
            message = ''
        
        if exc_info is None:
            content = ''
        elif isinstance(exc_info, basestring):
            content = exc_info
        else:
            content = [m.strip() for m in traceback.format_exception(*exc_info)]
            replace_message = True if not message or message.strip() == content[-1].strip() else False
            try:
                content[-1], _ = content[-1].split(':', 1)
            except ValueError:
                pass
            if replace_message:
                message = '{} in {}'.format(content[-1], content[-2].splitlines()[0].strip()[5:])
            content = '\n'.join(content)

        if message is None or message == '':
            message = content.splitlines()[-1].strip()

        for source in re.finditer(r'[/\\]extern[/\\]([^/\\]+)', content):
            source = source.group(1)
            if source in patch.sources and patch.sources[source] not in log_targets:
                log_targets.append(patch.sources[source])

        if not log_targets and patch.core_source is not None:
            log_targets.append(patch.core_source)

        def anonymize(c):
            c = c.replace(settings.app_dir, '{APP_DIR}')
            c = c.replace(settings.data_dir, '{DATA_DIR}')
            c = c.replace(settings.home_dir, '{HOME_DIR}')
            c = re.sub(r'/[^\s]+/lib/python([23](\.\d+)?)?', 'lib', c)
            return c

        message = anonymize(message)
        content = anonymize(content)

        name = re.sub(r'\s(\d+|[\da-f]{32})$',  ' ###', self.name)

        data = [None, name, type, message, content]
        data[0] = hashlib.sha1(''.join(unicode(s) for s in data[1:]).replace('\r', '')).hexdigest()

        for target in log_targets:
            gevent.spawn(target.send_error, *data)

# some logbal functions

def getLogger(name):
    return logging.getLogger(name)
get = getLogger

def var_to_level(level):
    if isinstance(level, basestring):
        level = level.upper()
        return getattr(logging, level, None)
    return level

# catch unhandled exceptions

unhandled_exception_log = None
ignore_exceptions = [SystemExit, gevent.GreenletExit]

def excepthook(type, value, tb, *args, **kwargs):
    #traceback.print_exception(type, value, tb)
    if type in ignore_exceptions:
        return
    if type == KeyboardInterrupt:
        get('logger').critical('caught keyboard interrupt. exit')
        ignore_exceptions.append(KeyboardInterrupt)
        #sys.exit(0)
    else:
        kwargs['exc_info'] = type, value, tb
        unhandled_exception_log.unhandled_exception(*args, **kwargs)

# some libs

original_excepthook = sys.excepthook
OriginalHub = gevent.hub.Hub

class ErrorLoggingHub(OriginalHub):
    def print_exception(self, context, type, value, tb):
        #traceback.print_exception(type, value, tb)
        if context is not None and not isinstance(context, str):
            try:
                context = self.format_context(context)
            except:
                try:
                    context = repr(context)
                except:
                    context = __builtins__.type(context)
            else:
                context = '%s failed with %s' % (context, getattr(type, '__name__', 'exception'))
        try:
            excepthook(type, value, tb, context if unicode(context) else 'unhandled exception')
        except:
            traceback.print_exc()

# file handler functions

consolehandler = None
filehandler = None

def set_filelog():
    global filehandler

    if filehandler:
        logging.getLogger().removeHandler(filehandler)
        filehandler = None

    if config['log_file'] is not None:
        filehandler = logging.handlers.WatchedFileHandler(config['log_file'], 'a')
        filehandler.setFormatter(formatter)
        filehandler.setLevel(config['log_file_level'])
        logging.getLogger().addHandler(filehandler)

def set_consolelog():
    global consolehandler

    if consolehandler:
        logging.getLogger().removeHandler(consolehandler)
        consolehandler = None

    if file is not None:
        consolehandler = logging.StreamHandler()
        consolehandler.setFormatter(formatter)
        consolehandler.setLevel(config['log_console_level'])
        logging.getLogger().addHandler(consolehandler)

# config

class JsonFile(dict):
    def __init__(self, file, defaults=None):
        self._file = file
        if not isinstance(defaults, dict):
            defaults = dict()
        try:
            with open(file, 'rb') as f:
                data = f.read()
                data = json.loads(data)
                defaults.update(data)
            for k, v in data.iteritems():
                dict.__setattr__(self, k, v)
        except IOError:
            pass
        dict.__init__(self, defaults)

    def _save(self):
        data = json.dumps(self)
        with open(self._file, 'wb') as f:
            f.write(data)

    def __setattr__(self, k, v):
        dict.__setattr__(self, k, v)
        self._save()

config = None

# setup logger

formatter = logging.Formatter("%(asctime)s %(name)-40s %(levelname)-8s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logging.setLoggerClass(MyLogger)
logging.getLogger().setLevel(logging.DEBUG)

unhandled_exception_log = get('unhandled_exception')

# stdout/err redirect

class Std(object):
    def __init__(self, name, superobj, level=logging.DEBUG):
        self.level = level
        self.logger = get(name)
        self.superobj = superobj
        self.cache = ''

    def write(self, data):
        self.cache += data
        if not '\n' in data:
            return
        data = self.cache.rstrip()
        self.cache = ''
        if data:
            try:
                self.logger.log(self.level, data)
            except IOError:
                pass
            except:
                self.superobj.write(data)

    def __getattr__(self, item):
        return getattr(self.superobj, item)

# init functions

def init_pre():
    global config
    config = JsonFile(settings.log_settings_file, defaults=dict(log_file=settings.log_file, log_file_level=logging.DEBUG, log_console_level=logging.DEBUG))
    set_consolelog()
    set_filelog()

def init_optparser(parser, OptionGroup):
    from .localize import _T
    group = OptionGroup(parser, _T.logger__options, _T.logger__valid_levels)
    group.add_option('--log-level', dest="log_level", metavar="LEVEL", help=_T.logger__log_level)
    group.add_option('--log-file', dest="log_file", metavar="FILE", help=_T.logger__log_file)
    group.add_option('--log-file-level', dest="log_file_level", metavar="LEVEL", help=_T.logger__log_file_level)
    parser.add_option_group(group)

def init(options):
    sys.excepthook = lambda type, value, tb: excepthook(type, value, tb, 'unhandled exception in main loop')
    gevent.hub.Hub = ErrorLoggingHub
    gevent.get_hub().__class__ = ErrorLoggingHub

    if options is not None: # used for tests
        reload_console = False
        reload_file = False

        if options.log_level is not None:
            config['log_console_level'] = var_to_level(options.log_level)
            reload_console = True
    
        if options.log_file_level is not None:
            config['log_file_level'] = var_to_level(options.log_file_level)
            reload_file = True
        if options.log_file is not None:
            if options.log_file.lower() == 'off':
                config['log_file'] = None
            else:
                config['log_file'] = options.log_file
            reload_file = True

        if reload_console:
            set_consolelog()
        if reload_file:
            set_filelog()
    
    sys._old_stdout = sys.stdout
    sys._old_stderr = sys.stderr
    sys.stdout = Std("stdout", sys.stdout)
    sys.stderr = Std("stderr", sys.stderr, logging.ERROR)

def terminate():
    sys.excepthook = original_excepthook
    gevent.hub.Hub = OriginalHub
    gevent.get_hub().__class__ = OriginalHub
    sys.stdout = sys._old_stdout
    sys.stderr = sys._old_stderr
    atexit.register(logging.shutdown)
