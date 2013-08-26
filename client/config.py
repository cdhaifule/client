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

import json
import traceback

from gevent.lock import Semaphore

from . import event, scheme, settings, interface
from .scheme import transaction

class ConfigTable(scheme.Table):
    _table_name = 'config'

_defaults = dict()

class Config(object):
    def new(self, name):
        return SubConfig(self, name)

    def default(self, key, value, type=None, func=None, private=False, protected=False, allow_none=False, description=None, enum=None, hook=None):
        if not func is None:
            self.register_hook(key, func)
        if isinstance(enum, basestring):
            raise TypeError("must use a collection as enum not string")
        if enum is not None and not isinstance(enum, dict):
            enum = {i: None for i in enum}
        _defaults[key] = {
            'value': value,
            'type': type,
            'private': private,
            'protected': protected,
            'allow_none': value is None and True or allow_none,
            'description': description,
            'enum': enum}
        if hook is not None:
            self.register_hook(key, hook)
        if key not in _configtable._table_data:
            self[key] = value

    def register(self, key, _config=None):
        """decorator"""
        def f(func):
            self.register_hook(key, func, _config)
            return func
        return f

    def register_hook(self, key, func, _config=None):
        e = 'config.{}:changed'.format(key)
        cnt = func.func_code.co_argcount
        if cnt > 0 and func.func_code.co_varnames[0] == 'self':
            cnt -= 1
        if cnt == 0:
            cb = lambda e, table, old: func()
        elif cnt == 1:
            cb = lambda e, table, old: func(self[key])
        elif cnt == 2:
            cb = lambda e, table, old: func(self[key], old)
        elif cnt == 4:
            cb = lambda e, table, old: func(e, configobj(_config or self), self[key], old)
        else:
            raise ValueError('callback function must have 4 (event, config, value, old_value), 2 (value, old_value), 1 (value) or 0 arguments, not {}'.format(cnt))
        event.add(e, cb)

        # fire changed event directly when config is already initialized
        try:
            if module_initialized.is_set() and hasattr(self, key):
                cb(e, self, self[key])
        except NameError:
            pass

    def __getattr__(self, key):
        return getattr(_configtable, key)
    __getitem__ = __getattr__

    def __setattr__(self, key, value):
        try:
            getattr(_configtable, key)
        except AttributeError:
            if key in _defaults:
                enum = _defaults[key]["enum"]
                if enum is not None and value not in enum:
                    raise ValueError("value not allowed by enum definition")
                else:
                    if _defaults[key]["type"] == bool:
                        value = bool(value)
            if key in _defaults and (_defaults[key]['type'] is not None or _defaults[key]['allow_none'] is False):
                on_set = lambda _, value: self._on_set(key, value)
            else:
                on_set = None
            if key in _defaults and _defaults[key]['private']:
                col = scheme.Column('config', on_set=on_set, fire_event=True)
            else:
                col = scheme.Column(('config', 'api'), on_set=on_set, fire_event=True)
            setattr(_configtable.__class__, key, col)
            col.init_table_class(_configtable.__class__, key)
            col.init_table_instance(_configtable)
        with transaction:
            setattr(_configtable, key, value)
    __setitem__ = __setattr__

    def _on_set(self, key, value):
        d = _defaults[key]
        if d['type'] is None:
            return value
        if value is None:
            if d['allow_none'] is False:
                raise ValueError('config key {} must not be none'.format(key))
            return value
        if not isinstance(value, d['type']):
            if d['type'] == bool:
                if value == 'true':
                    return True
                if value == 'false':
                    return False
            if value == '':
                if d['type'] == int:
                    return 0
                if d['type'] == float:
                    return 0.0
            return d['type'](value)
        return value


class SubConfig(object):
    def __init__(self, config, name):
        self._config = config
        self._name = name
        self._defaults = set()

    def __iter__(self):
        return iter(self._defaults)
        
    def __contains__(self, i):
        return i in self._defaults
        
    def iteritems(self):
        return iter((i, self[i]) for i in self)
        
    def items(self):
        return list(self.iteritems())

    def new(self, name):
        return SubConfig(self, name)

    def default(self, key, *args, **kwargs):
        self._defaults.add(key)
        self._config.default('{}.{}'.format(self._name, key), *args, **kwargs)

    def register(self, key, _config=None):
        """decorator"""
        return self._config.register('{}.{}'.format(self._name, key), _config or self)

    def register_hook(self, key, func, _config=None):
        return self._config.register_hook('{}.{}'.format(self._name, key), func, _config or self)

    def __getattr__(self, key):
        #if key.startswith('_'):
        #    return object.__getattr__(self, key)
        return self._config.__getattr__('{}.{}'.format(self._name, key))
    __getitem__ = __getattr__

    def __setattr__(self, key, value):
        if key.startswith('_'):
            return object.__setattr__(self, key, value)
        self._defaults.add(key)
        return self._config.__setattr__('{}.{}'.format(self._name, key), value)
    __setitem__ = __setattr__

def configobj(config):
    class _Config(object):
        def __iter__(self):
            return config.__iter__()

        def __contains__(self, i):
            return i in config

        def iteritems(self):
            return config.iteritems()

        def items(self):
            return config.items()

        def new(self, name):
            return configobj(config.new(name))

        def default(self, key, *args, **kwargs):
            return config.default(key, *args, **kwargs)

        def register(self, key, _config=None):
            """decorator"""
            return config.register(key, _config)

        def register_hook(self, key, func, _config=None):
            return config.register_hook(key, func, _config)

        def get(self, key):
            if not key.startswith('_'):
                try:
                    return getattr(config, key)
                except AttributeError:
                    return self.new(key)
        __getattr__ = get
        __getitem__ = get

        def set(self, key, value):
            if not key.startswith('_'):
                return setattr(config, key, value)
        __setattr__ = set
        __setitem__ = set

    return _Config()

class ConfigListener(scheme.TransactionListener):
    lock = Semaphore()

    def on_commit(self, update):
        if settings.config_file:
            data = scheme.get_by_uuid(update.keys()[0]).serialize()
            if not data:
                return
            data = json.dumps(data, indent=4, sort_keys=True)
            with self.lock:
                with open(settings.config_file, 'w') as f:
                    f.write(data)

@interface.register
class Interface(interface.Interface):
    name = 'config'

    def set(key, value):
        if key in _defaults and (_defaults[key]['private'] or _defaults[key]['protected']):
            raise ValueError('access denied')
        with transaction:
            _config[key] = value

    @interface.protected
    def set_protected(key, value):
        with transaction:
            _config[key] = value

    def describe():
        """returns a dict like:
        type - variable type (int, float, string, boolean, array, hash)
        allow_none - is a value of 'null' allowed?
        protected - is this variable protected? (use config.set_protected to change this variable)
        default - default value
        description - description of this key
        """
        result = dict()
        
        weights = {key: i for i, key in enumerate(sorted(_defaults))}
            
        for key, value in _defaults.iteritems():
            if not value['private']:
                if value['type'] == int:
                    type = 'int'
                elif value['type'] == float:
                    type = 'float'
                elif value['type'] in (str, unicode):
                    type = 'string'
                elif value['type'] == bool:
                    type = 'boolean'
                elif value['type'] in (list, tuple, set):
                    type = 'list'
                elif value['type'] == dict:
                    type = 'dict'
                else:
                    raise RuntimeError('unknown variable type of key {}: {}'.format(key, value['type']))
                result[key] = dict(
                    weight=weights[key],
                    type=type,
                    default=value['value'],
                    protected=value['protected'],
                    allow_none=value['allow_none'],
                    description=value['description'],
                    enum=value['enum'])
        return result

@event.register('loader:initialized')
def _(e):
    for key, col in _configtable.__dict__.items():
        if isinstance(col, scheme.Column):
            col.on_changed(_defaults.get(key, None))

with transaction:
    _configtable = ConfigTable()
    _config = Config()
    config = configobj(_config)
    globalconfig = config

def init():
    if settings.config_file:
        try:
            data = open(settings.config_file, 'r').read()
            data = json.loads(data)
            event.fire('config:before_load', data)
            with transaction:
                for key, value in data.iteritems():
                    try:
                        _config[key] = value
                    except BaseException as e:
                        from . import logger
                        traceback.print_exc()
                        logger.get('config').critical('error loading config {} key {}: {}'.format(settings.config_file, key, e))
        except IOError:
            pass
        except BaseException as e:
            from . import logger
            traceback.print_exc()
            logger.get('config').critical('error loading config {}: {}'.format(settings.config_file, e))

    scheme.register(ConfigListener('config'))
    event.fire('config:loaded')
