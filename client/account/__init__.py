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

from .models import Account, Profile, HosterAccount, PremiumAccount, Http, HttpAccount, HttpHosterAccount, HttpPremiumAccount, \
    MultiAccount, HttpMultiAccount
from .manager import manager, log, config
from ..hoster.this import localctx
from ..scheme import transaction
from .. import db, interface, settings
from ..api import proto
from . import verify

def init():
    # plugins are loaded by hoster.py
    Account.localctx = localctx
    
    with transaction, db.Cursor() as c:
        aa = c.execute("SELECT * FROM account")
        for a in aa.fetchall():
            try:
                name = json.loads(a['name'])
                if name == "mega.co.nz":
                    raise TypeError("mega no support for now, deleting account")
                data = json.loads(a['data'])
                oldpw = data.pop('password', "") # TODO: remove. update process
                data['id'] = int(a['id'])
                pool = manager.get_pool(name)
                if oldpw:
                    # TODO: remove. update process
                    data["password"] = oldpw
                elif hasattr(pool.account_class, 'password'):
                    data["password"] = keyring.get_password(settings.keyring_service, "account_{}_password".format(a["id"])) or ''
                pool.add(**data)
            except TypeError:
                log.critical("broken row: {}".format(a))
                c.execute("DELETE FROM account WHERE id={}".format(a["id"]))
            except AttributeError:
                log.critical("hoster account for {} not exists anymore".format(name))

@interface.register
class AccountInterface(interface.Interface):
    name = "account"

    def add(name=None, **kwargs):
        """adds a new account
        generally the plugin name is needed: {name: 'plugin name'}
        for default hoster plugins additional: {username: 'name', password: 'pass'}
        for http profiles: {
            host: 'hostname', port: port, username: 'user', password: 'pass',
            auth_method: 'auth',
            cookies: {key: value, key2: value2},
            headers: {key: value, key2, value2}}
        for ftp profiles: {
            host: 'hostname', port: port, username: 'user', password: 'pass'}"""
        account = manager.get_pool(name).add(**kwargs)
        if account:
            return account.id

    def remove(id=None):
        """removes an account"""
        with transaction:
            try:
                pool, account = manager.get_account_by_id(int(id))
            except ValueError:
                pass # account already deleted (not found)
            else:
                pool.remove(account)

    def reset(id=None):
        """resets an account (logout and clear infos ...)"""
        with transaction:
            pool, account = manager.get_account_by_id(int(id))
            account.reset()

    def check(id=None):
        """rechecks an account (makes reset, than check)"""
        with transaction:
            pool, account = manager.get_account_by_id(int(id))
            account.reset()
        account.boot()
    recheck = check

    def modify(id=None, update=None):
        """modifies files. arguments are the same as on modify_package"""
        pool, account = manager.get_account_by_id(int(id))
        with transaction:
            enabled = account.enabled
            account.reset()
            account.enabled = enabled
            account.modify_table(update)
        account.boot()

    def sync(clients=None):
        for name, pool in manager.iteritems():
            for acc in pool:
                if acc._private_account:
                    continue
                data = acc.get_login_data()
                if not data:
                    continue
                data['name'] = name
                data['enabled'] = acc.enabled
                for client in clients:
                    if client == settings.app_uuid:
                        continue
                    proto.send('client', 'account.add', payload=data, channel=client)

    def list_plugins():
        """lists all account plugins"""
        return list(manager)
        
    def set_secret(hoster=None, code=None, timeleft=None):
        verify.set_secret(hoster, code, timeleft)
