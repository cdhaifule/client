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

from functools import wraps

from .defaults import hoster as default_hoster, http as default_http, premium as default_premium
from .this import localctx
from . import manager

from .. import account, logger
from ..config import globalconfig
from ..scheme import Column

pluginconfig = globalconfig.new('hoster')

class cfg(object):
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

def _compile_config(ctx, code):
    for c in code:
        if isinstance(c, cfg):
            ctx.default(*c.args, **c.kwargs)
        else:
            next = ctx.new(c[0])
            _compile_config(next, c[1:])

def wrap(hoster, func):
    @wraps(func)
    def f(*args, **kwargs):
        try:
            localctx.hoster = hoster
            ret = func(*args, **kwargs)
        finally:
            try:
                del localctx.hoster
            except AttributeError:
                pass
        return ret
    return f

class Hoster(object):
    multihoster = False
    defaults = [default_hoster]
    account_model = account.Account
    multihoster = False
    
    def __init__(self, module):
        # setup module
        self.module = module
        module.this.hoster = self
        
        uses = getattr(module.this.options, "uses", None)
        if not uses:
            self.uses = None
        elif isinstance(uses, basestring):
            self.uses = [uses]
        elif isinstance(uses, list):
            self.uses = uses
        else:
            pass

        # setup account
        if hasattr(module.this.options, 'account_model'):
            account_model = module.this.account_model
        else:
            account_model = self.account_model

        class Account(account_model):
            hoster = self
            
            def on_initialize(account):
                sua = self.set_user_agent
                if sua:
                    if isinstance(sua, dict):
                        account.set_user_agent(**sua)
                    else:
                        account.set_user_agent()
                return self.on_initialize_account(account)

        if hasattr(module, 'extra_persistent_account_columns'):
            for key in module.extra_persistent_account_columns:
                assert not hasattr(Account, key), 'Column {} already exists'.format(key)
                setattr(Account, key, Column('db'))

        self.accounts = account.manager.get_pool(module.this.name, Account)

        # setup log
        self.log = logger.get("hoster:{}:".format(module.this.name))
        
        # setup config
        config = self.config
        if config:
            realconfig = pluginconfig.new(self.name)
            _compile_config(realconfig, config)
            module.this.options.config = realconfig
        
        search = self.search
        if search:
            try:
                tags = search.get("tags", None)
            except AttributeError:
                module.this.options.search = None
                self.log.warning("plugin search must be dict for config")
            else:
                if not tags:
                    tags = ["other"]
                else:
                    if isinstance(tags, basestring):
                        tags = map(str.strip, tags.split(","))
                search["tags"] = tags
                if not config:
                    realconfig = pluginconfig.new(self.name)
                    module.this.options.config = realconfig
                realconfig.default("default_phrase", 
                    search.get("default_phrase", ""), str, 
                    description="Default search phrase")

    def get_account(self, task, file):
        if self.accounts is None:
            return None
        return self.accounts.get_best(task, file)

    def __getattr__(self, item):
        if hasattr(self.module.this.options, item):
            return getattr(self.module.this.options, item)
        for i in self.chain():
            try:
                f = getattr(i, item)
            except AttributeError:
                continue
            else:
                if callable(f):
                    f = wrap(self, f)
                return f
        try:
            return getattr(self.module.this, item)
        except AttributeError:
            raise AttributeError("plugin '{}' has no attribute '{}'".format(self.name, item))
    def chain(self, ignore_module=False):
        if not ignore_module:
            yield self.module
        if self.uses:
            for m in self.uses:
                yield manager.find_by_name(m).module
        for i in self.defaults:
            yield i

class _Http(object):
    defaults = [default_http]

class HttpHoster(_Http, Hoster):
    defaults = _Http.defaults + Hoster.defaults
    account_model = account.HttpAccount

class PremiumHoster(Hoster):
    defaults = [default_premium] + Hoster.defaults
    account_model = account.PremiumAccount

class HttpPremiumHoster(_Http, PremiumHoster):
    defaults = _Http.defaults + PremiumHoster.defaults
    account_model = account.HttpPremiumAccount

class MultiHoster(PremiumHoster):
    multihoster = True
    account_model = account.MultiAccount

class MultiHttpHoster(HttpPremiumHoster):
    multihoster = True
    account_model = account.HttpMultiAccount

MultiHttpPremiumHoster = MultiHttpHoster