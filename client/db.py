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
import keyring
import sqlite3

from . import scheme, settings, logger, event
from gevent.lock import Semaphore

conn = None
lock = Semaphore()
log = logger.get('db')

version = 20

class Cursor:
    def __init__(self):
        self.c = conn.cursor()

    def __enter__(self):
        return self.c

    def __exit__(self, *args):
        with lock:
            conn.commit()

def _create(c, sql):
    try:
        c.execute(sql)
    except sqlite3.OperationalError as e:
        if not str(e).endswith(' already exists'):
            raise

class SqlListener(scheme.TransactionListener):
    def __init__(self):
        scheme.TransactionListener.__init__(self, 'db')
        self.known_ids = set()

    def on_commit(self, update):
        with Cursor() as c:
            for uid, data in update.iteritems():
                uid = data['table']+str(data['id'])
                if data['action'] in ('new', 'update'):
                    self.on_update(c, uid, data)

                elif data['action'] == 'delete':
                    self.on_delete(c, uid, data)

    def execute(self, c, q, values):
        for i in range(len(values)):
            if values[i] not in (True, False, None):
                values[i] = json.dumps(values[i])
        try:
            c.execute(q, values)
        except BaseException as e:
            if str(e) != 'PRIMARY KEY must be unique' and str(e) != 'column id is not unique':
                log.critical('DB ERROR: {}'.format(e))
                log.critical('DB ERROR: {}'.format(q))
                log.critical('DB ERROR: {}'.format(values))
            raise

    def on_update(self, c, uid, data):
        table = data['table']

        del data['action']
        del data['table']

        if 'last_error' in data:
            t = scheme.get_by_uuid(data['id'])
            if hasattr(t, 'next_try') and t.next_try not in (None, False):
                del data['last_error']
                if len(data.keys()) == 1: # is only id left?
                    return

        if table == 'account':
            t = scheme.get_by_uuid(data['id'])
            assert t._table_name == table
            if t._private_account:
                return
            data = {'id': data['id'], 'name': t.name, 'data': t.serialize(set(['db']), set(['id', 'name']))}

        elif table == 'patch_source':
            t = scheme.get_by_uuid(data['id'])
            assert t._table_name == table
            d = t.serialize(set(['db']), set(['id']))
            d['enabled'] = t._table_data['enabled'].value
            data = {'id': data['id'], 'data': d}

        elif table == 'file':
            if 'completed_plugins' in data:
                data['completed_plugins'] = list(data['completed_plugins'])
            if 'name' in data:
                t = scheme.get_by_uuid(data['id'])
                data['name'] = t._table_data['name'].value
            if 'url' in data:
                t = scheme.get_by_uuid(data['id'])
                data['url'] = t._table_data['url'].value

        keys = data.keys()

        if uid not in self.known_ids:
            try:
                q = "INSERT INTO "+table+" ({columns}) VALUES ({values})".format(
                    columns='"'+'", "'.join(keys)+'"',
                    values=', '.join(['?' for i in range(len(keys))]))
                self.execute(c, q, data.values())
                self.known_ids.add(uid)
                return
            except sqlite3.IntegrityError as e:
                if str(e) != 'PRIMARY KEY must be unique' and str(e) != 'column id is not unique':
                    raise
                self.known_ids.add(uid)

        q = "UPDATE "+table+" SET {columns} WHERE id='{id}'".format(
            columns='"'+'"=?, "'.join(keys)+'"=?',
            id=json.dumps(data['id']))
        self.execute(c, q, data.values())

    def on_delete(self, c, uid, data):
        q = "DELETE FROM "+data['table']+" WHERE id=?"
        try:
            self.execute(c, q, [data['id']])
        except sqlite3.OperationalError as e:
            if not str(e).startswith('no such data: '):
                raise
        if uid in self.known_ids:
            self.known_ids.remove(uid)

def _dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d


class PasswordListener(scheme.TransactionListener):
    def __init__(self):
        scheme.TransactionListener.__init__(self, 'password')
    
    def on_commit(self, update):
        for key, data in update.iteritems():
            for k, v in data.iteritems():
                if k in {"action", "table", "id"}:
                    continue
                key = "{}_{}_{}".format(data["table"], data["id"], k)
                if data["action"] in {"new", "update"}:
                    keyring.set_password(settings.keyring_service, key, v or "")
                elif data["action"] == "delete":
                    keyring.delete_password(settings.keyring_service, key)


listener = None

def init_pre():
    scheme.register(PasswordListener())

def init():
    global conn
    global listener

    create_statements = dict()
    create_statements['version'] = """CREATE TABLE %s (
        version INTEGER)"""
    create_statements['package'] = """CREATE TABLE %s (
        id UNICODE(100) PRIMARY KEY,
        name UNICODE(500),
        download_dir UNICODE(1000),
        complete_dir UNICODE(1000),
        extract_dir UNICODE(1000),
        extract UNICODE(10),
        extract_passwords UNICODE(200),
        position BIGINT,
        state UNICODE(15),
        system UNICODE(15),
        payload UNICODE(5000),
        last_error UNICODE(100))"""
    create_statements['file'] = """CREATE TABLE %s (
        id UNICODE(100) PRIMARY KEY,
        package UNICODE(100),
        name UNICODE(100),
        size BIGINT,
        approx_size BIGINT,
        weight BIGINT,
        position BIGINT,
        enabled UNICODE(10),
        state UNICODE(15),
        last_error UNICODE(100),
        completed_plugins UNICODE(1000),
        url UNICODE(1000),
        extra UNICODE(1000),
        referer UNICODE(1000),
        hash_type UNICODE(10),
        hash_value UNICODE(100))"""
    create_statements['chunk'] = """CREATE TABLE %s (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file INTEGER,
        begin BIGINT,
        end BIGINT,
        pos BIGINT,
        state UNICODE(15))"""
    create_statements['account'] = """CREATE TABLE %s (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name UNICODE(100),
        data UNICODE(5000))"""
    create_statements['patch_source'] = """CREATE TABLE %s (
        id UNICODE(100) PRIMARY KEY,
        data BLOB)"""

    conn = sqlite3.connect(settings.db_file)
    conn.row_factory = _dict_factory

    #conn.row_factory =  sqlite3.Row
    with Cursor() as c:
        for table in create_statements:
            _create(c, create_statements[table] % table)

    ############################### update code
    with Cursor() as c:
        db_version = c.execute("SELECT version FROM version").fetchone()
    if db_version is None:
        with Cursor() as c:
            c.execute("INSERT INTO version VALUES (?)", [version])
        db_version = version
    else:
        db_version = int(db_version['version'])
    old_version = db_version

    """update "templates"
    # add a column
    c.execute("ALTER TABLE file RENAME TO file_old_%s" % db_version)
    _create(c, create_file % 'file')
    old_columns = ['id', 'name', 'size', 'approx_size'] # old columns
    c.execute("INSERT INTO file (%s) SELECT %s FROM file_old_%s" % (old_columns, old_columns, db_version))

    # remove a column
    c.execute("ALTER TABLE file RENAME TO file_old_%s" % db_version)
    _create(c, create_file % 'file')
    new_columns = ['id', 'name', 'size', 'approx_size'] # new columns
    c.execute("INSERT INTO file (%s) SELECT %s FROM file_old_%s" % (new_columns, new_columns, db_version))

    # rename a column
    c.execute("ALTER TABLE file RENAME TO file_old_%s" % db_version)
    _create(c, create_file % 'file')
    old_columns = ['id', 'name', 'size', 'approx_size']
    new_columns = ['id', 'name', 'size', 'foobar_size']
    c.execute("INSERT INTO file (%s) SELECT %s FROM file_old_%s" % (new_columns, old_columns, db_version))
    """

    def update_20(c):
        @event.register('loader:initialized')
        def on_loader_initialized(e):
            import sys
            from . import input
            text = list()
            text.append("You have a very outdated database version of download.am.")
            text.append("Please note that we have reset your database to make this version work.")
            text.append("We are very sorry for that.")
            elements = list()
            for t in text:
                elements.append([input.Text(text)])
            elements.append([input.Text('')])
            elements.append([input.Submit('OK')])
            input.get(elements, type='sorry', timeout=None, close_aborts=True, ignore_api=True)
            for t in text:
                print >>sys.stderr, t

        for table in create_statements:
            c.execute("DROP TABLE %s" % table)
        for table in create_statements:
            _create(c, create_statements[table], table)

    updates = list()
    for key in locals().keys():
        if key.startswith('update_'):
            updates.append(int(key.rsplit('_', 1)[1]))
    updates.sort()

    for update_version in updates:
        if db_version < update_version:
            log.info('current version {}, running update to version {}, target version {}'.format(db_version, update_version, version))
            with Cursor() as c:
                locals()['update_{}'.format(update_version)](c)
            db_version = update_version

    if old_version != db_version:
        with Cursor() as c:
            c.execute("UPDATE version SET version=?", [db_version])
        log.info('changed database version from {} to {}'.format(old_version, db_version))

    # create and register our listener
    listener = SqlListener()
    register_listener()

def register_listener():
    scheme.register(listener)

def unregister_listener():
    scheme.unregister(listener)
