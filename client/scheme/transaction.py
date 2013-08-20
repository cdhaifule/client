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

import gevent

from .. import event

class TransactionError(BaseException):
    pass

def merge_transaction_data(a, b):
    for uid, data in b.iteritems():
        if uid in a and a[uid]['action'] == 'delete':
            continue
        elif not uid in a or data['action'] == 'delete':
            a[uid] = data
        else:
            action = a[uid]['action']
            a[uid].update(data)
            a[uid]['action'] = action

class TransactionColumn(object):
    def __init__(self, column):
        self.column = column
        v = column.value
        if isinstance(v, list):
            v = v[:]
        elif isinstance(v, dict):
            w = {}
            w.update(v)
            v = w
        self.old = v

    def __cmp__(self, other):
        return self.column != other and 1 or 0

    def __hash__(self):
        return hash(self.column)

class IndexList(object):
    def __init__(self):
        self.idx = dict()
        self.lst = list()

    def append(self, obj):
        self.idx[hash(obj)] = obj
        self.lst.append(obj)

    def remove(self, obj):
        del self.idx[hash(obj)]
        self.lst.remove(obj)

    def reverse(self):
        self.lst.reverse()

    def __getitem__(self, key):
        return self.lst[key]

    def __setitem__(self, key, value):
        self.lst[key] = value

    def __delitem__(self, key):
        del self.idx[hash(self.lst[key])]
        del self.lst[key]

    def __iter__(self):
        return self.lst.__iter__()

    def __contains__(self, obj):
        return hash(obj) in self.idx

class TransactionData(object):
    def __init__(self):
        self.new = IndexList()
        self.dirty = IndexList()
        self.delete = IndexList()


class TransactionManager(list):
    def add(self, listener):
        self.append(listener)

    def commit(self, data):
        for table in data.new:
            if table._table_created_event:
                event.fire('{}:created'.format(table._table_name), table)

        for t in data.dirty:
            t.column.column.on_changed(t.column.table, t.old)

        for table in data.delete:
            if table._table_deleted_event:
                event.fire('{}:deleted'.format(table._table_name), table)

        for listener in self:
            result = dict()

            for t in data.dirty:
                col = t.column
                if not (listener.channels & col.column.channels):
                    continue
                uid = col.table._uuid
                if uid not in result:
                    result[uid] = {
                        'table': col.table._table_name,
                        'id': col.table.id,
                        'action': col.table in data.new and 'new' or 'update'}
                result[uid][col.column.name] = col.column.get_value(col.table)

            for table in data.delete:
                if not (listener.channels & table._table_channels):
                    continue
                result[table._uuid] = {
                    'table': table._table_name,
                    'id': table.id,
                    'action': 'delete'}

            if result:
                listener.commit(result)


class Transaction(object):
    def __init__(self, manager):
        self.manager = manager
        self.chains = {}

    def acquire(self):
        chain = self.get_new_chain()
        chain.append(TransactionData())

    def release(self):
        chain = self.get_chain()
        data = chain.pop()
        if len(chain) > 0:
            # merge transaction with last one
            next = chain[-1]
            self.merge(next, data)
        else:
            self.remove_chain()
            self.manager.commit(data)

    def release_all(self):
        try:
            while True:
                self.release()
        except TransactionError:
            pass

    def abort(self):
        print "!"*100, 'ABORT TRANSACTION'
        chain = self.get_chain()
        data = chain.pop()

        data.new.reverse()
        for table in data.new:
            table._table_remove_from_dict()
            table._table_deleted = True
            if table._table_collection and table in table.__class__._table_collection:
                if type(table.__class__._table_collection) == dict:
                    del table.__class__._table_collection[table.id]
                else:
                    table.__class__._table_collection.remove(table)
            #TODO: foreign keys

        data.delete.reverse()
        for table in data.delete:
            table._table_add_to_dict()
            table._table_deleted = False
            if table._table_collection and not table in table.__class__._table_collection:
                if type(table.__class__._table_collection) == dict:
                    table.__class__._table_collection[table.id] = table
                else:
                    table.__class__._table_collection.append(table)
            #TODO: foreign keys

        data.dirty.reverse()
        for t in data.dirty:
            if not t.column.table._table_deleted and not t.column.column.always_use_getter:
                t.column.column.set_value(t.column.table, t.old, _set_dirty=False)
                t.column.refresh_cache = True

    def __enter__(self):
        self.acquire()

    def __exit__(self, extype, exvalue, extb):
        if extype:
            #import traceback
            #traceback.print_exc(extb)
            self.abort()
        else:
            self.release()

    def merge(self, a, b):
        """merges data of b into a"""
        for table in b.new:
            self._set_new(table, a)
        for table in b.delete:
            self._set_delete(table, a)
        for t in b.dirty:
            if not t.column in a.dirty and not t.column.table in a.delete:
                t.column.refresh_cache = True
                a.dirty.append(t)

    def get_new_chain(self):
        id = hash(gevent.getcurrent())
        if not id in self.chains:
            self.chains[id] = []
        return self.chains[id]

    def get_chain(self):
        id = hash(gevent.getcurrent())
        if id not in self.chains:
            raise TransactionError('table changed without transaction')
        return self.chains[id]

    def remove_chain(self):
        del self.chains[hash(gevent.getcurrent())]

    def set_dirty(self, column):
        data = self.get_chain()[-1]
        self._set_dirty(data, column)

    def _set_dirty(self, data, column):
        if column.table in data.delete:
            raise TransactionError('set on a deleted table: {}'.format(column.table))
        s = hash(column)
        if s not in data.dirty:
            data.dirty.append(TransactionColumn(column))
        column.refresh_cache = True
        for key in column.column.change_affects:
            if isinstance(key, basestring):
                col = column.table._table_data[key]
            else:
                t = getattr(column.table, key[0])
                if t is None:
                    continue
                col = t._table_data[key[1]]
            self._set_dirty(data, col)

    def set_new(self, table):
        data = self.get_chain()[-1]
        self._set_new(table, data)

    def _set_new(self, table, data):
        if table in data.delete:
            raise TransactionError('new table can not be in delete transaction')
        if not table in data.new:
            data.new.append(table)

    def set_delete(self, table):
        data = self.get_chain()[-1]
        self._set_delete(table, data)

    def _set_delete(self, table, data):
        if table in data.new:
            data.new.remove(table)
            #TODO: call table.table_delete() ?
        elif not table in data.delete:
            data.delete.append(table)
        for t in data.dirty[:]:
            if t.column.table == table:
                data.dirty.remove(t)


class TransactionListener(object):
    def __init__(self, channels):
        if type(channels) == set:
            self.channels = channels
        elif type(channels) in (list, tuple):
            self.channels = set(channels)
        elif channels is None:
            self.channels = set()
        else:
            self.channels = set([channels])

    def commit(self, update):
        self.on_commit(update)

    def on_commit(self, update):
        raise NotImplementedError()

class PassiveListener(TransactionListener):
    def __init__(self, channels):
        TransactionListener.__init__(self, channels)
        self.update = dict()

    def commit(self, update):
        if self.update:
            merge_transaction_data(self.update, update)
        else:
            self.update = update

    def do_commit(self):
        update = self.update
        self.update = dict()
        update = self.on_commit(update) or dict()
        if update:
            self.commit(update)

    def get(self):
        return self.update

    def clear(self):
        self.update = dict()

    def pop(self):
        update = self.get()
        self.clear()
        return update

class DelayedListener(PassiveListener):
    def __init__(self, channels, delay):
        PassiveListener.__init__(self, channels)
        self.delay = delay
        self.greenlet = None

    def commit(self, update):
        PassiveListener.commit(self, update)
        if self.update and (not self.greenlet or self.greenlet.dead):
            self.greenlet = gevent.spawn_later(self.delay, self.do_commit)

    def do_commit(self):
        try:
            PassiveListener.do_commit(self)
        finally:
            self.greenlet = None


manager = TransactionManager()
register = manager.add
unregister = manager.remove

transaction = Transaction(manager)


import json
class DebugListener(DelayedListener):
    def on_commit(self, update):
        print "DebugListener:"
        for data in update.values():
            print "--- {:6}: {:7} {:4}".format(data['action'], data['table'], data['id']),
            del data['action']
            del data['table']
            del data['id']
            print json.dumps(data, sort_keys=True)
        print
