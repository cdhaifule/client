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
import hashlib
import requests
import dateutil
import urlparse
import time

import keyring
from gevent.lock import Semaphore

from .manager import config, manager
from .. import useragent, event, logger, settings, scheme
from ..cache import CachedDict
from ..scheme import transaction, Table, Column
from ..plugintools import ErrorFunctions, InputFunctions, ctx_error_handler, wildcard
from ..contrib import sizetools
from ..variablesizepool import VariableSizePool

class Account(Table, ErrorFunctions, InputFunctions):
    """on ErrorFunctions member functions the variable need_reconnect is ignored
    """
    _table_name = 'account'
    _table_created_event = True
    _table_deleted_event = True

    _private_account = False

    id = Column(('api', 'db'))
    name = Column(('api', 'db'))
    enabled = Column(('api', 'db'), fire_event=True, read_only=False)
    last_error = Column(('api', 'db'))
    next_try = Column('api', lambda self, value: not value is None and int(value.eta*1000) or value, fire_event=True)
    multi_account = Column('api')

    hoster = None       # must be set before hoster.register()

    # none means use defaults from hoster class, some value means account.foo > hoster.foo and hoster.foo or account.foo
    max_check_tasks = Column(always_use_getter=True)
    max_download_tasks = Column(always_use_getter=True)
    max_chunks = Column(always_use_getter=True)
    can_resume = Column(always_use_getter=True)

    def __init__(self, **kwargs):
        self.account = self # needed for InputFunctions.solve_* functions

        self.multi_account = False

        self.lock = Semaphore()
        self.check_pool = VariableSizePool(size=self.max_check_tasks)
        self.download_pool = VariableSizePool(size=self.max_download_tasks)
        self.search_pool = VariableSizePool(size=10)
        self.reset()

        for k, v in kwargs.iteritems():
            setattr(self, k, v)

    def __eq__(self, other):
        return isinstance(other, Account) and self.name == other.name

    def get_login_data(self):
        """returns dict with data for sync (clone accounts on other clients)
        """
        return dict()

    def match(self, file):
        return True

    def reset(self):
        """reset (logout ...) account"""
        with transaction:
            self.on_reset()

    def on_reset(self):
        self.max_check_tasks = None
        self.max_download_tasks = None
        self.max_chunks = None
        self.can_resume = None
        self.check_pool.set(self.max_check_tasks)
        self.download_pool.set(self.max_download_tasks)
        self.enabled = True
        self.reset_retry()
        self._initialized = False
        self._last_check = None

    def on_changed_next_try(self, old):
        if self.next_try is None:
            gevent.spawn(self.boot)

    def on_get_next_try(self, value):
        return None if value is None else value.eta

    def boot(self, return_when_locked=False):
        if return_when_locked and self.lock.locked():
            return
        with self.lock:
            if not self.enabled:
                #TODO: raise GreenletExit ?
                return
            if self.next_try is not None:
                #TODO: raise GreenletExit ?
                return
            with transaction:
                self.last_error = None
            if not self._initialized or self._last_check is None or self._last_check + config['recheck_interval'] < time.time():
                self.initialize()
                self.check_pool.set(self.max_check_tasks)
                self.download_pool.set(self.max_download_tasks)
                
    def reboot(self):
        self.reset()
        self.boot()

    def initialize(self):
        transaction.acquire()
        try:
            ctx_error_handler(self, self.on_initialize)
            self._initialized = True
            event.fire('account:initialized', self)
        except:
            event.fire('account:initialize_error', self)
            raise
        finally:
            self._last_check = time.time()
            transaction.release()

    def on_initialize(self):
        raise NotImplementedError()

    @property
    def log(self):
        if not hasattr(self, '_log'):
            self._log = logger.get("account {}".format(self.id))
        return self._log

    @property
    def weight(self):
        try:
            self.boot()
            if not self.enabled:
                return None
            if self.next_try:
                return None
            return self.on_weight()
        except gevent.GreenletExit:
            return None

    _captcha_values = {
        True: 0,
        None: 1,
        False: 2}

    def on_weight(self):
        """"returns none when not useable
        """
        return [
            1,
            self._captcha_values[self.has_captcha],
            1000000 if self.max_download_speed is None else int(self.max_download_speed/50),
            None if self.waiting_time is None else int(self.waiting_time/60)]

    def fatal(self, msg):
        with transaction:
            self.last_error = msg
            self.enabled = False
        self.log.error(msg)
        raise gevent.GreenletExit()

    def login_failed(self, msg=None):
        """default error message for failed logins"""
        if msg:
            self.fatal('login failed: {}'.format(msg))
        else:
            self.fatal('login failed')

    def retry(self, msg, seconds, _=False): # _ is a placeholder to make ErrorFunctions work. we have no need_reconnect
        with transaction:
            self.next_try = gevent.spawn_later(seconds, self.reset_retry)
            self.next_try.eta = time.time() + seconds
            self.last_error = msg
        self.log.info('retry in {} seconds: {}'.format(seconds, msg))
        raise gevent.GreenletExit()

    def reset_retry(self):
        with transaction:
            self.next_try = None
            self.last_error = None

    def get_task_pool(self, task):
        if task == 'check':
            return self.check_pool
        elif task == 'download':
            return self.download_pool
        elif task == 'search':
            return self.search_pool
        else:
            raise RuntimeError('unknown task pool: {}'.format(task))

    # preferences from hoster.this

    # max_check_tasks
    def on_get_max_check_tasks(self, value):
        self.boot(True)
        return self.hoster.max_check_tasks if value is None else min(value, self.hoster.max_check_tasks)

    # max_download_tasks
    def on_get_max_download_tasks(self, value):
        self.boot(True)
        return self.hoster.max_download_tasks if value is None else min(value, self.hoster.max_download_tasks)

    # max_chunks
    def on_get_max_chunks(self, value):
        self.boot(True)
        return self.hoster.max_chunks if value is None else min(value, self.hoster.max_chunks)

    def on_get_can_resume(self, value):
        self.boot(True)
        return self.hoster.can_resume if value is None else value and self.hoster.can_resume

    @property
    def max_filesize(self):
        return self.hoster.max_filesize

    @property
    def max_download_speed(self):
        return self.hoster.max_download_speed

    @property
    def has_captcha(self):
        return self.hoster.has_captcha

    @property
    def waiting_time(self):
        return self.hoster.waiting_time

    def on_check_decorator(self, func, *args, **kwargs):
        return func(*args, **kwargs)

    def on_download_decorator(self, func, *args, **kwargs):
        return func(*args, **kwargs)

    on_download_next_decorator = on_download_decorator
        

class PasswordListener(scheme.TransactionListener):
    def __init__(self):
        scheme.TransactionListener.__init__(self, 'password')
    
    def on_commit(self, update):
        for key, data in update.iteritems():
            for k, v in data.iteritems():
                if k in {"action", "table", "id"}: continue
                key = "{}_{}_{}".format(data["table"], data["id"], k)
                if data["action"] in {"new", "update"}:
                    keyring.set_password(settings.keyring_service, key, data["password"] or "")
                elif data["action"] == "delete":
                    keyring.delete_password(settings.keyring_service, key)
        

class Profile(Account):
    _all_hostnames = wildcard('*')

    # match variables
    hostname = Column(('api', 'db'), read_only=False, fire_event=True)
    port = Column(('api', 'db'), read_only=False, fire_event=True)

    # options
    username = Column(('api', 'db'), read_only=False, fire_event=True)
    password = Column("password", read_only=False, fire_event=True, always_use_getter=True)

    def __init__(self, **kwargs):
        Account.__init__(self, **kwargs)
        if self.hostname:
            self._hostname = wildcard(self.hostname)
        else:
            self._hostname = self._all_hostnames

    def __eq__(self, other):
        return isinstance(other, Profile) and self.name == other.name and self.hostname == other.hostname and self.port == other.port and self.username == other.username

    def get_login_data(self):
        data = Account.get_login_data(self)
        data.update(dict(hostname=self.hostname, port=self.port, username=self.username, password=self.password))
        return data

    def match(self, file):
        if not self._hostname.match(file.split_url.host):
            return False
        if not self.port is None and self.port != file.split_url.port:
            return False
        return True

    def on_weight(self):
        w = Account.on_weight(self)
        if w is None:
            return None
        if self._hostname == self._all_hostnames:
            return w
        if self.username is None:
            w[0] += 1
            return w
        w[0] += 2
        return w


#from requests.models import Response
#from requests.packages.urllib3.response import HTTPResponse
#sbox.type_manager.add(Response, leave=['close'])

class HosterAccount(Account):
    username = Column(('api', 'db'), read_only=False, fire_event=True)
    password = Column("password", read_only=False, fire_event=True, always_use_getter=True)

    def __init__(self, username=None, **kwargs):
        Account.__init__(self, **kwargs)
        self.username = username

    def on_weight(self):
        w = Account.on_weight(self)
        if w is None:
            return w
        if self.username is not None:
            w[0] += 1
        return w

    def on_initialize(self):
        pass

class PremiumAccount(HosterAccount):
    premium = Column('api', always_use_getter=True)
    expires = Column('api')
    traffic = Column('api')
    max_traffic = Column('api')

    def __init__(self, **kwargs):
        HosterAccount.__init__(self, **kwargs)
        self.max_traffic = 0

    def get_login_data(self):
        data = HosterAccount.get_login_data(self)
        data.update(dict(username=self.username, password=self.password))
        return data

    def on_set_traffic(self, value):
        value = isinstance(value, basestring) and sizetools.human2bytes(value) or value
        if value is not None and value > self.max_traffic:
            with transaction:
                self.max_traffic = value
        return value

    def on_set_expires(self, value):
        return isinstance(value, basestring) and time.mktime(dateutil.parser.parse(value).timetuple()) or value

    def on_get_premium(self, premium):
        if not premium:
            return premium
        if self.expires and self.expires < time.time():
            return False
        if self.traffic and self.traffic <= 0:
            return False
        return True

    def __eq__(self, other):
        return isinstance(other, HosterAccount) and self.name == other.name and self.username == other.username

    def on_reset(self):
        """reset (logout ...) account
        """
        HosterAccount.on_reset(self)
        self.premium = None
        self.expires = None
        self.traffic = None
        self.check_pool.set(self.hoster.max_check_tasks_free)
        self.download_pool.set(self.hoster.max_download_tasks_free)

    def on_initialize(self):
        raise NotImplementedError()
    
    @property
    def is_expired(self):
        self.boot()
        if self.expires is None or self.expires > time.time():
            return False
        return True

    @property
    def has_traffic(self):
        return self.traffic is None or self.traffic > 0

    def on_weight(self):
        w = HosterAccount.on_weight(self)
        if w is None:
            return w
        if self.premium:
            w[0] += 1
        if not self.is_expired:
            w[0] += 1
        if self.has_traffic:
            w[0] += 1
        return w

    def get_max_chunks(self):
        if self.premium:
            return self.hoster.max_chunks_premium
        else:
            return self.hoster.max_chunks_free
            
    def set_buy_url(self, url):
        from ..hoster.util import buy_premium
        return buy_premium(self.hoster.name, url)

    # preferences from hoster.this

    def on_get_max_check_tasks(self, value):
        self.boot(True)
        h = self.hoster.max_check_tasks_premium if self.premium else self.hoster.max_check_tasks_free
        return h if value is None else min(value, h)

    def on_get_max_download_tasks(self, value):
        self.boot(True)
        h = self.hoster.max_download_tasks_premium if self.premium else self.hoster.max_download_tasks_free
        return h if value is None else min(value, h)

    def on_get_max_chunks(self, value):
        self.boot(True)
        h = self.hoster.max_chunks_premium if self.premium else self.hoster.max_chunks_free
        return h if value is None else min(value, h)

    def on_get_can_resume(self, value):
        self.boot(True)
        h = self.hoster.can_resume_premium if self.premium else self.hoster.can_resume_free
        return h if value is None else value and h

    @property
    def max_filesize(self):
        # TODO: implement this variable
        self.boot()
        return self.hoster.max_filesize_premium if self.premium else self.hoster.max_filesize_free

    @property
    def max_download_speed(self):
        self.boot()
        return self.hoster.max_download_speed_premium if self.premium else self.hoster.max_download_speed_free

    @property
    def has_captcha(self):
        self.boot()
        return self.hoster.has_captcha_premium if self.premium else self.hoster.has_captcha_free

    @property
    def waiting_time(self):
        self.boot()
        return self.hoster.waiting_time_premium if self.premium else self.hoster.waiting_time_free


class Http(object):
    http_garbage = dict()

    def __init__(self):
        self._browser = None
        self._user_agent = None

    def on_reset(self):
        """reset (logout ...) account"""
        self._browser = None
        self._user_agent = None
        self._response_cache = None

    def get_browser(self):
        if self._browser is None:
            self._browser = requests.session()
        return self._browser

    @property
    def browser(self):
        return self.get_browser()

    @property
    def cookies(self):
        return self.get_browser().cookies

    @property
    def headers(self):
        return self.get_browser().headers

    def set_user_agent(self, os=None, browser=None, user_agent=None):
        if user_agent:
            self._user_agent = {'User-Agent': user_agent}
        else:
            self._user_agent = useragent.get(os, browser)

    def reset_user_agent(self):
        self._user_agent = None

    @property
    def response_cache(self):
        if self._response_cache is None:
            self._response_cache = CachedDict(livetime=30)
        return self._response_cache

    def _http_request_prepare(self, kwargs):
        if not 'headers' in kwargs:
            kwargs['headers'] = dict()

        if self._user_agent:
            for key, value in self._user_agent.iteritems():
                if key not in kwargs['headers']:
                    kwargs['headers'][key] = value

        if 'chunk' in kwargs:
            if kwargs['chunk'] is not None and (kwargs['chunk'].pos or kwargs['chunk'].end):
                kwargs['range'] = [kwargs['chunk'].pos, kwargs['chunk'].end]
            del kwargs['chunk']
        
        set_exact_range = kwargs.pop('set_exact_range', False)

        if 'range' in kwargs:
            if kwargs['range'] is not None and (kwargs['range'][0] > 0 or set_exact_range):
                if set_exact_range:
                    kwargs['headers']['Range'] = 'bytes={pos}-{end}'.format(
                        pos=kwargs['range'][0],
                        end=int(kwargs['range'][1])-1 if kwargs['range'][1] else '')
                else:
                    kwargs['headers']['Range'] = 'bytes={pos}-'.format(pos=kwargs['range'][0], end=kwargs['range'][1] and kwargs['range'][1] or '')
            del kwargs['range']

        if 'referer' in kwargs:
            if kwargs['referer'] is not None:
                kwargs['headers']['Referer'] = kwargs['referer']
            del kwargs['referer']

    def _http_cache_id(self, params, enum):
        for key, value in enum:
            params.append(str(key))
            if isinstance(value, dict):
                self._http_cache_id(params, enumerate(value))
            elif isinstance(value, (tuple, list, set)):
                self._http_cache_id(params, value.iteritems())
            else:
                params.append(str(value))

    def _decorate_resp_functions(self, resp, func):
        def fn(url, **kwargs):
            url = urlparse.urljoin(resp.url, url)
            if 'referer' not in kwargs:
                kwargs['referer'] = resp.url
            return func(url, **kwargs)
        return fn

    def _http_request(self, func, url, **kwargs):
        self._http_request_prepare(kwargs)

        # check cache
        if 'use_cache' in kwargs:
            use_cache = kwargs['use_cache']
            del kwargs['use_cache']
            if use_cache:
                params = list()
                self._http_cache_id(params, dict(url=url))
                self._http_cache_id(params, kwargs.iteritems())
                params.sort()
                cache_id = hashlib.md5(' -- '.join(params)).hexdigest()
                if cache_id in self.response_cache:
                    resp = self.response_cache[cache_id]
                    resp.from_cache = True
                    return resp
        else:
            use_cache = False

        # make request
        resp = func(url, **kwargs)

        # set cache
        resp.from_cache = False
        if use_cache:
            self.response_cache[cache_id] = resp

        # handle garbage collector
        if kwargs.get('stream', False):
            g = id(gevent.getcurrent())
            if g in Http.http_garbage:
                Http.http_garbage[g].add(resp)
                resp._o_close = resp.close
                def close():
                    try:
                        Http.http_garbage[g].remove(resp)
                    except KeyError:
                        pass
                    resp.close = resp._o_close
                    resp.close()
                resp.close = close
            #else:
            #    self.log.warning('using requests with stream=True, but no garbage context is set')

        # fake requests (account) object
        resp.account = self
        resp.get = self._decorate_resp_functions(resp, self.get)
        resp.post = self._decorate_resp_functions(resp, self.post)

        return resp

    def get(self, url, **kwargs):
        return self._http_request(self.browser.get, url, **kwargs)

    def post(self, url, **kwargs):
        return self._http_request(self.browser.post, url, **kwargs)

    def on_check_decorator(self, func, *args, **kwargs):
        g = id(gevent.getcurrent())
        Http.http_garbage[g] = set()
        try:
            return func(*args, **kwargs)
        finally:
            for resp in Http.http_garbage[g]:
                resp._o_close()
            del Http.http_garbage[g]

    def on_download_decorator(self, func, *args, **kwargs):
        g = id(gevent.getcurrent())
        Http.http_garbage[g] = set()
        result = None
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            for resp in Http.http_garbage[g]:
                if resp != result:
                    resp._o_close()
            del Http.http_garbage[g]
    on_download_next_decorator = on_download_decorator


class MultiAccount(PremiumAccount):
    multi_account = Column('api')
    compatible_hostnames = Column('api')

    compatible_plugins = [] # string matched existing plugin names
    compatible_hosts = []   # regex matched hosts

    def __init__(self, *args, **kwargs):
        PremiumAccount.__init__(self, *args, **kwargs)
        self.multi_account = True
    
    def set_compatible_hosts(self, hosts):
        self.compatible_plugins = []
        self.compatible_hosts = []
        h = []
        for name in hosts:
            if name == 'uploaded.to':
                name = 'uploaded.net'
            if name in manager:
                self.compatible_plugins.append(name)
            else:
                self.compatible_hosts.append(wildcard('*.{}'.format(name)))
                self.compatible_hosts.append(wildcard(name))
                h.append(name)
        with transaction:
            self.compatible_hostnames = hosts
        self.log.debug('mulit hoster fallback hosts: {}'.format(', '.join(self.compatible_plugins)))
        self.log.debug('mulit hoster direct hosts: {}'.format(', '.join(h)))


class HttpAccount(Http, Account):
    def __init__(self, **kwargs):
        Account.__init__(self, **kwargs)
        Http.__init__(self)

    def on_reset(self):
        Account.on_reset(self)
        Http.on_reset(self)

class HttpHosterAccount(Http, HosterAccount):
    def __init__(self, **kwargs):
        HosterAccount.__init__(self, **kwargs)
        Http.__init__(self)

    def on_reset(self):
        HosterAccount.on_reset(self)
        Http.on_reset(self)

class HttpPremiumAccount(Http, PremiumAccount):
    def __init__(self, **kwargs):
        PremiumAccount.__init__(self, **kwargs)
        Http.__init__(self)

    def on_reset(self):
        PremiumAccount.on_reset(self)
        Http.on_reset(self)

class HttpMultiAccount(Http, MultiAccount):
    
    def __init__(self, *args, **kwargs):
        MultiAccount.__init__(self, *args, **kwargs)
        Http.__init__(self)
        self.multi_account = True
        
        
    def on_reset(self):
        MultiAccount.on_reset(self)
        Http.on_reset(self)
