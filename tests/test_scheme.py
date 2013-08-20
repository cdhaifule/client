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

from client import scheme
from client.scheme import transaction, TransactionError
from client.scheme.transaction import PassiveListener

class DebugListener(PassiveListener):
    def pop(self):
        update = PassiveListener.pop(self)
        #self.dump(update)
        return update

    def dump(self, update):
        print "DebugListener:"
        for data in update.values():
            print "--- {:6}: {:7} {:4}".format(data['action'], data['table'], data['id']),
            print json.dumps(data, sort_keys=True)
        print

class TableTest(scheme.Table):
    _table_name = 'test'

    test = scheme.Column('test')
    evented = scheme.Column('test', fire_event=True)
    getter = scheme.Column('test', lambda self, old: self.getter and self.getter['foo'] or None)
    listed = scheme.Column('test')
    dicted = scheme.Column('test')

    def __init__(self, **kwargs):
        for k, v in kwargs.iteritems():
            setattr(self, k, v)

def test_scheme():
    listener = DebugListener('test')
    scheme.register(listener)

    with transaction:
        test = TableTest(test="hallo", evented="this is an event", getter={"foo": "bar"}, listed=['abcdef'])
    data = listener.pop()
    si = data.keys()[0] - 1
    assert data == {si+1: {'evented': 'this is an event', 'dicted': None, 'test': 'hallo', 'getter': 'bar', 'listed': ['abcdef'], 'action': 'new', 'table': 'test', 'id': si+1}}

    with transaction:
        test2 = TableTest(test="hallo test 2", evented="this is an event", getter={"foo": "bar"}, listed=['abcdef'])
    assert listener.pop() == {si+2: {'evented': 'this is an event', 'dicted': None, 'test': 'hallo test 2', 'getter': 'bar', 'listed': ['abcdef'], 'action': 'new', 'table': 'test', 'id': si+2}}

    with transaction:
        with transaction:
            test3 = TableTest(test="hallo test 3")
        test3.listed = ['foo', 'bar']
    assert listener.pop() == {si+3: {'evented': None, 'dicted': None, 'test': 'hallo test 3', 'getter': None, 'listed': ['foo', 'bar'], 'action': 'new', 'table': 'test', 'id': si+3}}

    with transaction:
        with transaction:
            test3.table_delete()
    data = listener.pop()
    assert data == {si+3: {'action': 'delete', 'table': 'test', 'id': si+3}}

    with transaction:
        with transaction:
            test3 = TableTest(test="hallo test 3")
        with transaction:
            test3.table_delete()
    assert listener.pop() == {}

    with transaction:
        test.dicted = {"hallo": "welt"}
    assert listener.pop() == {si+1: {'action': 'update', 'table': 'test', 'id': si+1, 'dicted': {'hallo': 'welt'}}}

    with transaction:
        test.listed.append('ghijkl')
        with transaction:
            test.dicted['ficken'] = 'klar'
    assert listener.pop() == {si+1: {'action': 'update', 'table': 'test', 'dicted': {'hallo': 'welt', 'ficken': 'klar'}, 'id': si+1, 'listed': ['abcdef', 'ghijkl']}}

    with transaction:
        test.listed += ["mnopq"]
        test.dicted['ficken'] = 'logo'
    assert listener.pop() == {si+1: {'action': 'update', 'table': 'test', 'dicted': {'hallo': 'welt', 'ficken': 'logo'}, 'id': si+1, 'listed': ['abcdef', 'ghijkl', 'mnopq']}}

    with transaction:
        test.listed.remove('ghijkl')
        del test.dicted['ficken']
        test.dicted.update({"arsch": "loch"})
    assert listener.pop() == {si+1: {'action': 'update', 'table': 'test', 'dicted': {'hallo': 'welt', 'arsch': 'loch'}, 'id': si+1, 'listed': ['abcdef', 'mnopq']}}

    try:
        with transaction:
            test.listed.remove('abcdef')
            del test.dicted['arsch']
            raise ValueError('foo')
    except ValueError:
        pass
    assert listener.pop() == {}

    with transaction:
        with transaction:
            test2.table_delete()
    assert listener.pop() == {si+2: {'action': 'delete', 'table': 'test', 'id': si+2}}

    try:
        with transaction:
            test.table_delete()
            raise ValueError('foo')
    except ValueError:
        pass
    assert listener.pop() == {}

    try:
        with transaction:
            test2.listed = None
        assert False
    except TransactionError:
        pass
    assert listener.pop() == {}

    try:
        with transaction:
            test2.listed.append('append on deleted table')
        assert False
    except TransactionError:
        pass
    assert listener.pop() == {}

    with transaction:
        test.table_delete()
    assert listener.pop() == {si+1: {'action': 'delete', 'table': 'test', 'id': si+1}}

if __name__ == '__main__':
    test_scheme()
