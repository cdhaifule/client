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

from gevent.local import local
from contextlib import contextmanager

from ..variablesizepool import VariableSizePool

localctx = local()

class host(object):
    __slots__ = ["options", "ignore_module", "check_pool", "download_pool"]
    
    def __init__(self, options):
        self.options = options()
        self.ignore_module = False
        self.check_pool = VariableSizePool(size=self.global_max_check_tasks)
        self.download_pool = VariableSizePool(size=self.global_max_download_tasks)

    def get_account(self, task, file):
        return localctx.hoster.get_account(task, file)
        
    @property
    def log(self):
        return localctx.hoster.log

    def __getattr__(self, attr):
        try:
            if self.options.name is not None:
                try:
                    if localctx.hoster.module != self.options.hoster.module:
                        #print "cross call", localctx.hoster.module, self.options.hoster.module, attr
                        h = localctx.hoster
                    else:
                        h = self.options.hoster
                except AttributeError:
                    h = self.options.hoster
            else:
                h = localctx.hoster
        except AttributeError: # we are getting called outside of hoster context
            try:
                return getattr(self.options, attr) # 1st: look in local options
            except AttributeError:
                if hasattr(self.options, "model"): # 2nd: look in models defaults
                    for d in self.options.model.defaults:
                        if hasattr(d, "this"):
                            try:
                                return getattr(d.this, attr)
                            except AttributeError:
                                pass
                raise
        else:
            #try:
            #    nh = localctx.hoster
            #    print "localcontext hoster", nh.module
            #except AttributeError:
            #    print "no localctx"
            #print "cc chain...,", self.options.name, attr, h, h.module, self.options.__dict__
            for i in h.chain(self.ignore_module):
                #print "\t", i
                try:
                    return getattr(i.this.options, attr)
                except AttributeError:
                    pass

        f = getattr(localctx.hoster, attr)
        if callable(f):
            h = localctx.hoster

            def with_context(*args, **kwargs):
                try:
                    oldh = localctx.hoster
                except AttributeError:
                    oldh = None
                try:
                    localctx.hoster = h
                    return f(*args, **kwargs)
                finally:
                    if oldh is not None:
                        localctx.hoster = oldh
            return with_context
        else:
            return f
    
    def __setattr__(self, attr, value):
        if attr in self.__slots__:
            object.__setattr__(self, attr, value)
        else:
            try:
                h = localctx.hoster
            except AttributeError:
                setattr(self.options, attr, value)
            else:
                setattr(h.module.this.options, attr, value)
    
    # super
    @property
    def super(self):
        """use: with this.super: this.func"""
        @contextmanager
        def _super():
            self.ignore_module = True
            yield
            self.ignore_module = False
        return _super()
