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

import bisect
import gevent

from gevent.pool import Group
from gevent.lock import Semaphore

from .. import event, logger
from ..scheme import transaction
from ..config import globalconfig

log = logger.get('account')

config = globalconfig.new('account')
config.default('recheck_interval', 300, int)
config.default('use_useraccounts', True, bool)
config.default('ask_buy_premium', True, bool)
config.default('ask_buy_premium_time', 7200, int)


class AccountManager(dict):
    def get_pool(self, name, account_class=None):
        """when the pool does not exist and account_class is set we create a new pool"""
        if not name in self and account_class:
            self[name] = AccountPool(name, account_class)
        try:
            return self[name]
        except KeyError:
            log.error("Account pool {} not found".format(name))
            
    def remove_pool(self, name):
        try:
            self[name].clear()
            del self[name]
        except KeyError:
            log.error("Account pool {} not found".format(name))

    def get_account_by_id(self, id):
        for pool in self.values():
            for account in pool:
                if int(account.id) == (id):
                    return pool, account
        raise ValueError('account not found')

class AccountPool(list):
    """holds accounts for the same hoster"""
    def __init__(self, name, account_class):
        list.__init__(self)
        self.name = name
        self.account_class = account_class
        self.log = log.getLogger(self.name)
        self.lock = Semaphore()

    def add(self, **kwargs):
        with transaction:
            kwargs['name'] = self.name
            account = self.account_class(**kwargs)
            if account in self:
                raise ValueError('account already exists: {} {}'.format(account, kwargs))
        self.append(account)
        gevent.spawn(account.boot)
        return account

    def remove(self, account):
        if account not in self:
            raise ValueError('account not exists')
        account.table_delete()
        list.remove(self, account)

    def clear(self):
        self[:] = []

    def get_best(self, task, file):
        with self.lock:
            if config.use_useraccounts:
                all_accounts = self
            else:
                all_accounts = [a for a in self if a._private_account]

            group = Group()
            for account in all_accounts:
                group.spawn(account.boot)
            group.join()

            all_accounts = [a for a in all_accounts if a._private_account or (a.enabled and a.last_error is None)]
            
            accounts = []
            best_weight = 0
            for account in all_accounts:
                if file is not None and not account.match(file):
                    continue
                try:
                    weight = account.weight
                except gevent.GreenletExit:
                    continue
                if weight is None or weight < best_weight:
                    continue
                if weight > best_weight:
                    accounts = []
                    best_weight = weight
                bisect.insort(accounts, (account.get_task_pool(task).full() and 1 or 0, len(accounts), account))

            if accounts:
                return accounts[0][2]
            if len(all_accounts) > 0:
                #self.log.warning('found no account. returning first one...')
                return all_accounts[0]
            else:
                self.log.info('found no account. creating a "free" account')
                account = self.add(_private_account=True)
                account.boot()
                return account

manager = AccountManager()


@event.register("reconnect:reconnecting")
def reset_pools(*args):
    for accs in manager.itervalues():
        for acc in accs:
            try:
                for adapter in acc._browser.adapters.itervalues():
                    adapter.poolmanager.clear()
            except AttributeError:
                pass

@event.register('foo:ip_changed')
def _(e, *args):
    """reset "free" (private) accounts
    """
    for pool in manager.values():
        for account in pool:
            if account._private_account:
                account.reset()
