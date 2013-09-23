# -*- coding: utf-8 -*-
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

from collections import deque

from .. import event, settings, logger
from .transaction import transaction, TransactionError

import sys
import gevent
from gevent.lock import Semaphore

lock = Semaphore()
log = logger.get('scheme')

all_tables = dict() # tables by _uuid

def get_by_uuid(uid):
    return all_tables[uid]

def _patch_method(dst, cls, name):
    fn = getattr(cls, name)

    def func(self, *args, **kwargs):
        if self._table_column.table._table_deleted:
            raise TransactionError('trying to set value on a deleted table')
        transaction.set_dirty(self._table_column) # can this cause an exception with foreign keys?
        return fn(self, *args, **kwargs)

    setattr(dst, name, func)

class PatchedObject(object):
    pass

class PatchedSet(PatchedObject, set):
    def __init__(self, col, *args, **kwargs):
        self.__dict__['_table_column'] = col
        set.__init__(self, *args, **kwargs)

_patch_method(PatchedSet, set, '__isub__')
_patch_method(PatchedSet, set, '__ixor__')
_patch_method(PatchedSet, set, '__rand__')
_patch_method(PatchedSet, set, '__reduce__')
_patch_method(PatchedSet, set, '__ror__')
_patch_method(PatchedSet, set, '__rsub__')
_patch_method(PatchedSet, set, '__rxor__')
_patch_method(PatchedSet, set, '__sub__')
_patch_method(PatchedSet, set, '__xor__')
_patch_method(PatchedSet, set, 'add')
_patch_method(PatchedSet, set, 'clear')
_patch_method(PatchedSet, set, 'difference')
_patch_method(PatchedSet, set, 'difference_update')
_patch_method(PatchedSet, set, 'discard')
_patch_method(PatchedSet, set, 'intersection')
_patch_method(PatchedSet, set, 'intersection_update')
_patch_method(PatchedSet, set, 'pop')
_patch_method(PatchedSet, set, 'remove')
_patch_method(PatchedSet, set, 'symmetric_difference')
_patch_method(PatchedSet, set, 'symmetric_difference_update')
_patch_method(PatchedSet, set, 'union')
_patch_method(PatchedSet, set, 'update')
_patch_method(PatchedSet, set, '__delattr__')
_patch_method(PatchedSet, set, '__setattr__')

class PatchedList(PatchedObject, list):
    def __init__(self, col, *args, **kwargs):
        self.__dict__['_table_column'] = col
        list.__init__(self, *args, **kwargs)

_patch_method(PatchedList, list, 'append')
_patch_method(PatchedList, list, 'remove')
_patch_method(PatchedList, list, 'extend')
_patch_method(PatchedList, list, 'insert')
_patch_method(PatchedList, list, 'pop')
_patch_method(PatchedList, list, 'reverse')
_patch_method(PatchedList, list, 'sort')
_patch_method(PatchedList, list, '__add__')
_patch_method(PatchedList, list, '__delslice__')
_patch_method(PatchedList, list, '__iadd__')
_patch_method(PatchedList, list, '__imul__')
_patch_method(PatchedList, list, '__rmul__')
_patch_method(PatchedList, list, '__setslice__')
_patch_method(PatchedList, list, '__delattr__')
_patch_method(PatchedList, list, '__setattr__')
_patch_method(PatchedList, list, '__delitem__')
_patch_method(PatchedList, list, '__setitem__')

class PatchedDict(PatchedObject, dict):
    def __init__(self, col, *args, **kwargs):
        self.__dict__['_table_column'] = col
        dict.__init__(self, *args, **kwargs)

_patch_method(PatchedDict, dict, 'clear')
_patch_method(PatchedDict, dict, 'fromkeys')
_patch_method(PatchedDict, dict, 'pop')
_patch_method(PatchedDict, dict, 'popitem')
_patch_method(PatchedDict, dict, 'setdefault')
_patch_method(PatchedDict, dict, 'update')
_patch_method(PatchedDict, dict, '__delattr__')
_patch_method(PatchedDict, dict, '__setattr__')
_patch_method(PatchedDict, dict, '__delitem__')
_patch_method(PatchedDict, dict, '__setitem__')

def patch(col, value):
    if isinstance(value, PatchedObject):
        return value
    elif isinstance(value, list):
        return PatchedList(col, value)
    elif isinstance(value, dict):
        return PatchedDict(col, value)
    elif isinstance(value, set):
        return PatchedSet(col, value)
    else:
        return value

class ColumnData(object):
    pass

def channels_to_set(channels):
    if type(channels) is set:
        return channels
    elif type(channels) in (list, tuple):
        return set(channels)
    elif channels is None:
        return set()
    else:
        return set([channels])

class Column(object):
    def __init__(self, channels=None, on_get=None, on_set=None, on_changed=None, read_only=True, fire_event=False,
            change_affects=None, always_use_getter=False, getter_cached=False, foreign_key=None):
        """read_only is only for api calls that would change that column
        getter_cached will cache return value of getter function until column is set dirty
        """
        self.channels = channels_to_set(channels)
        
        self.on_get = on_get
        self.set_hooks = deque()
        self.changed_hooks = deque()

        if on_set is not None:
            self.set_hooks.append(on_set)
        if on_changed is not None:
            self.changed_hooks.append(on_changed)

        self.fire_event = fire_event

        self.read_only = read_only
        self.change_affects = change_affects or []
        self.always_use_getter = always_use_getter
        self.getter_cached = getter_cached

        if foreign_key is not None and len(foreign_key) == 2:
            foreign_key.append(None)
        self.foreign_key = foreign_key

        self.name = None
        self.initialized = False

    # initialize ourself with an table class or instance

    def init_table_class(self, cls, name):
        """cls is a Table class object"""
        if self.initialized:
            return
        self.initialized = True

        self.name = name

        # setup getter function
        if self.on_get is None:
            on_get_func = 'on_get_{}'.format(self.name)
            if hasattr(cls, on_get_func):
                self.on_get = lambda table, value: getattr(table, on_get_func)(value)
            else:
                self.on_get = lambda table, value: value

        # setup getter cache
        if self.getter_cached:
            def getter_cache_func(table, value):
                data = table._table_data[self.name]
                if data.refresh_cache:
                    data.cache = original_on_get(table, value)
                    data.refresh_cache = False
                return data.cache
            original_on_get = self.on_get
            self.on_get = getter_cache_func

        # setup on_set instance hook
        on_set_func = 'on_set_{}'.format(self.name)
        if hasattr(cls, on_set_func):
            self.set_hooks.appendleft(lambda table, value: getattr(table, on_set_func)(value))

        # setup foreign key handling
        if self.foreign_key:
            def handle_foreign_key(table, old, new):
                if old:
                    f = getattr(old, self.foreign_key[1])
                    if table in f:
                        f.remove(table)
                        # call the foreign key callback when collection is empty
                        if len(f) == 0 and self.foreign_key[2]:
                            self.foreign_key[2](table, old)
                if new:
                    f = getattr(new, self.foreign_key[1])
                    if table not in f:
                        f.append(table)
            self.handle_foreign_key = handle_foreign_key
        else:
            self.handle_foreign_key = lambda table, old, new: None

        # setup on_changed instance hook
        on_changed_func = 'on_changed_{}'.format(self.name)
        if hasattr(cls, on_changed_func):
            self.changed_hooks.appendleft(lambda table, old: getattr(table, on_changed_func)(old))

        # on changed event
        if self.fire_event:
            on_changed_event = '{}.{}:changed'.format(cls._table_name, self.name)
            self.changed_hooks.append(lambda table, old: event.fire(on_changed_event, table, old))

    def init_table_instance(self, table):
        """init a table instance and setup data and hooks"""
        # create our column data
        data = ColumnData()
        data.table = table
        data.column = self
        data.value = None
        data.cache = None
        data.refresh_cache = True
        table._table_data[self.name] = data

        # update table channels
        table._table_channels |= self.channels

    # register hooks

    def setter(self, func):
        self.set_hooks.append(func)
        return func

    def remove_setter(self, func):
        self.set_hooks.remove(func)

    def changed(self, func):
        self.changed_hooks.append(func)
        return func

    def remove_changed(self, func):
        self.changed_hooks.remove(func)

    # main getter/setter

    def __get__(self, table, owner):
        if table is None:
            return self
        if self.always_use_getter:
            return self.get_value(table)
        return table._table_data[self.name].value

    def __set__(self, table, value):
        #if self.always_use_getter:
        #    raise RuntimeError('not allowed to change value of a read only column (always_use_getter)')
        self.set_value(table, value)

    # get/set/changed functions

    def get_value(self, table):
        return self.on_get(table, table._table_data[self.name].value)

    def set_value(self, table, value, _set_dirty=True):
        if table._table_deleted:
            raise TransactionError('trying to set value on a deleted table')

        old = table._table_data[self.name].value
        if old == value:
            return

        for hook in self.set_hooks:
            value = hook(table, value)

        table._table_data[self.name].value = patch(table._table_data[self.name], value)

        self.handle_foreign_key(table, old, value)

        if _set_dirty:
            if table._table_auto_transaction:
                with transaction:
                    transaction.set_dirty(table._table_data[self.name])
            else:
                transaction.set_dirty(table._table_data[self.name])

    def on_changed(self, table, old):
        for hook in self.changed_hooks:
            hook(table, old)


def _write_uid(i, retry=2):
    try:
        with open(settings.next_uid_file, 'wb+') as f:
            f.write(str(i))
    except BaseException as e:
        if not retry:
            log.critical("could not write to app folder: {}".format(e))
            sys.exit(0)
        else:
            gevent.sleep(1)
            _write_uid(i, retry-1)
    
def get_next_uid():
    with lock:
        if type(settings.next_uid_file) == int:
            settings.next_uid_file += 1
            return settings.next_uid_file
        else:
            try:
                with open(settings.next_uid_file, 'r') as f:
                    uid = int(f.read())
            except IOError:
                uid = 1
            except ValueError:
                uid = 1
                
            _write_uid(uid+1)
            
            return uid

class Table(object):
    _table_name = None
    _table_collection = None
    _table_created_event = False
    _table_deleted_event = False
    _table_auto_transaction = False

    def __new__(cls, *args, **kwargs):
        new = object.__new__(cls, *args, **kwargs)

        # assign some internal variables
        new._table_channels = set()
        new._table_deleted = False

        new._table_data = dict()

        # initialize the base class
        """if not hasattr(cls, '_table_columns'):
            cls._table_columns = dict()
            cc = [cls]
            for c in cc:
                if issubclass(c, Table):
                    for key, col in c.__dict__.iteritems():
                        if isinstance(col, Column) and key not in cls._table_columns:
                            col.init_table_class(cls, key)
                    cc += c.__bases__"""

        columns = list()
        for c in cls.__mro__:
            if issubclass(c, Table):
                for key, col in c.__dict__.iteritems():
                    if isinstance(col, Column):
                        col.init_table_class(cls, key)
                        columns.append(col)

        # initialize this instance
        for col in columns:
            col.init_table_instance(new)

        # assign uuid
        if 'id' in kwargs and kwargs['id'] is not None:
            new._uuid = kwargs['id']
        else:
            new._uuid = get_next_uid()

        # set all columns dirty (performance killer, about 20%)
        for col in new._table_data.itervalues():
            transaction.set_dirty(col)

        # set the table id
        if not hasattr(new, 'id') or new.id is None:
            new.id = new._uuid

        # add instance to our collection
        if new._table_collection is not None:
            if isinstance(new.__class__._table_collection, dict):
                new.__class__._table_collection[new.id] = new
            else:
                new.__class__._table_collection.append(new)

        # add to main collection
        all_tables[new._uuid] = new

        # set table dirty
        transaction.set_new(new)

        return new

    def set_column_dirty(self, name):
        transaction.set_dirty(self._table_data[name])

    def get_column_value(self, name):
        return getattr(self.__class__, name).get_value(self)

    def set_table_dirty(self, channels=None, ignore_columns=None):
        channels = channels_to_set(channels)
        for key in dir(self.__class__):
            col = getattr(self.__class__, key)
            if isinstance(col, Column) and col.channels and (not channels or channels & col.channels) and (ignore_columns is None or col.name not in ignore_columns):
                transaction.set_dirty(self._table_data[key])

    def serialize(self, channels=None, ignore_columns=None):
        channels = channels_to_set(channels)
        data = dict()
        for key in dir(self.__class__):
            col = getattr(self.__class__, key)
            if isinstance(col, Column) and col.channels and (not channels or channels & col.channels) and (ignore_columns is None or col.name not in ignore_columns):
                data[key] = col.get_value(self)
        return data

    #def __str__(self):
    #    return json.dumps(self.serialize(), sort_keys=True)

    def match_filter(self, channels=None, not_filter=None, **filter):
        channels = channels_to_set(channels)
        match = lambda key, v: str(getattr(self, key)) == str(v)
        for k, v in filter.iteritems():
            col = getattr(self.__class__, k)
            if channels and not (channels & col.channels):
                raise KeyError('key {}.{} is not in namespace {}'.format(self._table_name, k, channels))
            list_matched = False
            if isinstance(v, tuple) or isinstance(v, list) or isinstance(v, set):
                for w in v:
                    if match(k, w):
                        list_matched = True
                        break
            if not match(k, v) and not list_matched:
                return False
        if not_filter is not None:
            if self.match_filter(channels, not_filter=None, **not_filter):
                return False
        return True

    def modify_table(self, update):
        for k, v in update.iteritems():
            col = getattr(self.__class__, k)
            if col.read_only:
                raise KeyError('key {}.{} is read only'.format(self._self_name, k))
            if col.foreign_key:
                raise ValueError('not allowed to change foreign variables')
        changed = False
        for k, v in update.iteritems():
            col = getattr(self.__class__, k)
            if unicode(self._table_data[k].value) != unicode(v):
                setattr(self, k, v)
                changed = True
        return changed

    def table_delete(self):
        # check if we were already deleted
        if self._table_deleted:
            return
        self._table_deleted = True
        del all_tables[self._uuid]
        
        # tell listeners that we are dead
        transaction.set_delete(self)

        # remove ourself from our collection
        if self._table_collection:
            if isinstance(self.__class__._table_collection, dict):
                del self.__class__._table_collection[self.id]
            else:
                self.__class__._table_collection.remove(self)

        # remove ourself from our foreign keys
        foreign_cb = list()
        for key in dir(self.__class__):
            col = getattr(self.__class__, key)
            if isinstance(col, Column) and col.foreign_key:
                value = self._table_data[key].value
                if isinstance(value, Table):
                    foreign = getattr(value, col.foreign_key[1])
                    if self in foreign:
                        foreign.remove(self)
                        # run callback when our foreign object has no more items from ourself
                        if not foreign and col.foreign_key[2]:
                            def func(callback, v):
                                return lambda: callback(self, v)
                            foreign_cb.append(func(col.foreign_key[2], value))

        # run the foreign empty callback
        if foreign_cb:
            for func in foreign_cb:
                func()

    def _table_add_to_dict(self):
        """internal"""
        all_tables[self._uuid] = self

    def _table_remove_from_dict(self):
        """internal"""
        del all_tables[self._uuid]


def filter_objects_callback(objects, filter, func):
    for obj in objects:
        if obj.match_filter('api', **filter):
            func(obj)
