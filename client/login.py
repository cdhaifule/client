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

import sys
import json
import base64
import gevent
import string
import random
import hashlib
import requests

from . import event, interface, logger, input, ui, settings
from .contrib import gibberishaes
from .scheme import transaction
from .config import globalconfig
from .localize import _T

from gevent.event import Event
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP
from Crypto import Random

module_initialized = Event()

log = logger.get("login")

hash_types = ['login', 'frontend', 'backend', 'client', 'protected']

config = globalconfig.new('login')

config.default('current', 'guest', str, private=True)

config.guest.default('username', '', unicode, private=True)
config.account.default('username', '', unicode, private=True)

hashes = dict()
for h in hash_types:
    hashes[h] = None
    config.guest.default(h, '', str, private=True, use_keyring=True)
    config.account.default(h, '', str, private=True, use_keyring=True)

_config_loaded = False


@event.register('config:before_load')
def on_config_before_load(e, data):
    if 'login.first_start' in data:
        if data['login.first_start']['frontend'] == data['login.hashes.frontend']:
            config.current = 'guest'
            data.pop('login.username', None)
        else:
            config.current = 'account'
        if isinstance(data['login.first_start'], basestring):
            data['login.first_start'] = json.loads(data['login.first_start'])
        for h in hash_types + ['username']:
            value = data['login.first_start'].get(h, None)
            if value:
                config.guest[h] = value
        del data['login.first_start']

    for h in hash_types:
        key = 'login.hashes.{}'.format(h)
        if key in data:
            config.account[h] = data[key] or ''
            del data[key]
    if 'login.username' in data:
        config.account.username = data['login.username']
        del data['login.username']

    if 'login.save_password' in data:
        del data['login.save_password']


def current():
    return config[config.current]

def sha256(s):
    if isinstance(s, unicode):
        s = s.encode("utf-8")
    return hashlib.sha256(s).hexdigest()

def hash_login(username, password):
    data = []
    data.append(password[::-1])
    data.append(sha256(password + username))
    data.append(sha256(username)[:len(password)])
    data.append(username[::-1])
    return sha256(''.join(data))

def hash_frontend(username, password, hash_login):
    return sha256(hash_login + username + password + sha256(password))

def hash_protected(password, hash_frontend):
    return sha256(sha256(password) + password + hash_frontend)

def hash_client(hash_login, hash_frontend):
    return sha256(hash_login + hash_frontend)

def set_login(username, password, type='account'):
    global _config_loaded
    _config_loaded = True

    if config.current == 'guest' and type == 'guest' and config.guest.username == username and has_login():
        return

    with transaction:
        config[type]['username'] = username
        if not username:
            for h in hash_types:
                config[type][h] = ''
        else:
            config[type]['login'] = hash_login(username, password)
            config[type]['frontend'] = hash_frontend(username, password, config[type]['login'])
            config[type]['backend'] = ''
            config[type]['protected'] = hash_protected(password, config[type]['frontend'])
            config[type]['client'] = hash_client(config[type]['login'], config[type]['frontend'])

    if has_login() and config.current == 'guest' and type == 'account':
        from . import api
        if api.client.is_connected():
            api.proto.send('frontend', 'website.setlocation', payload=dict(url=get_sso_url(type='account')))

    config.current = type

    event.fire('login:changed')
    
def generate_backend_key():
    if not has_login():
        raise RuntimeError('setting backend key while no login is set')
    key = current()['backend']
    if not key:
        key = sha256(Random.new().read(32))
        current()['backend'] = key
    return key

def get_sso_url(tab=None, type=None):
    return "https://{}/sso#{}".format(settings.frontend_domain, get_auth_token(tab, type=type))

def logout():
    if config.current == 'guest':
        login_dialog(exit_on_error=False, logout_current=False)
    else:
        with transaction:
            if config.current == 'guest':
                config.current = 'account'
            for h in hash_types:
                config['account'][h] = ''
        event.fire('login:changed')


login_event = Event() # event is set when login data is present

def has_login(type=None):
    c = current() if type is None else config[type]
    for h in hash_types + ['username']:
        if h != 'backend' and not c[h]:
            return False
    return True

def is_guest():
    """returns true if account is not setup with hashes or it's a guest account"""
    if config.current == 'guest':
        return True
    if not has_login():
        return True
    return False

def wait():
    login_event.wait()

def get(h):
    wait()
    return current()[h]

def get_auth_token(tab=None, type=None):
    """returns base64 encoded auto token for automatic login to website
    """
    char_set = string.ascii_uppercase + string.ascii_lowercase + string.digits
    key = ''.join(random.sample(char_set*12, 12))

    c = current() if type is None else config[type]
    data = "{};{};{};{}".format(c.username, c.login, c.frontend, settings.app_uuid)
    if tab is not None:
        data = '{};{}'.format(data, tab)
    data = base64.b64encode(gibberishaes.encrypt(key, data))
    data = data.replace('+', '-').replace('/', '_').replace('=', ',')
    return data+'!'+key

def encrypt(destination, data):
    key = get(destination)
    if key:
        return gibberishaes.encrypt(key, data)
    else:
        return data

source_decrypt_retries = dict()

def decrypt(source, data):
    key = get(source)
    if key:
        try:
            return gibberishaes.decrypt(key, data)
        except:
            source_decrypt_retries[source] = source_decrypt_retries.get(source, 0) + 1
            if source_decrypt_retries[source] > 3:
                source_decrypt_retries.clear()
                logout()
            log.critical('encryption key for source {} seems to be invalid'.format(source))
            raise
    else:
        return data

@event.register('config:loaded')
def on_config_loaded(e):
    global _config_loaded
    if _config_loaded:
        return
    _config_loaded = True

login_input = None

@event.register('login:changed')
def on_login_changed(e):
    global login_input
    module_initialized.wait()
    ui.module_initialized.wait()
    
    if has_login():
        login_event.set()
        if login_input is not None:
            login_input.kill()
            login_input = None
    else:
        if login_input is not None:
            return
        login_dialog()

def login_dialog(username=None, exit_on_error=True, logout_current=True, allow_guest_login=True, timeout=None):
    global login_input
    if not ui.ui.has_ui:
        log.error("Cannot login without user interface. For commandline usage see --help.")
        if exit_on_error:
            sys.exit(1)
        return
    if login_input is not None:
        log.info("Login dialog already active.")
        return
    if not username:
        username = config.account.username
    if logout_current: # don't clear the login event when we try a soft login...
        login_event.clear()

    elements = list()
    elements.append([input.Text('Please enter your login informations')])
    elements.append([input.Float('left')])
    elements.append([input.Text('')])
    elements.append([input.Text('E-Mail:'), input.Input('username', value=username)])
    elements.append([input.Text('Password:'), input.Input('password', 'password')])
    elements.append([input.Text(''), input.Link('https://{}/#pwlose'.format(settings.frontend_domain), 'Forgot password?')])
    elements.append([input.Text(''), input.Link('https://{}/#register'.format(settings.frontend_domain), 'Register')])
    elements.append([input.Float('right')])
    elements.append([input.Text('')])

    login_choices = [
        dict(value='ok', content='OK', ok=True),
        dict(value='cancel', content='Cancel', cancel=True),
    ]
    if allow_guest_login:
        login_choices.append(dict(value='guest', content='Connect as guest'))
    
    elements.append([input.Choice('action', choices=login_choices)])

    def _login_input():
        global login_input
        try:
            result = input.get(elements, type='login', timeout=timeout, close_aborts=True, ignore_api=True)
        except (input.InputAborted, input.InputTimeout):
            if exit_on_error:
                sys.exit(1)
            return
        else:
            if result['action'] == 'cancel':
                if exit_on_error:
                    sys.exit(1)
                return
            elif result['action'] == 'guest':
                init_first_start(1)
            else:
                set_login(result['username'], result['password'])
        finally:
            login_input = None

    login_input = gevent.spawn(_login_input)

def init_optparser(parser, OptionGroup):
    group = OptionGroup(parser, _T.login__options)
    group.add_option('--username', dest="username", help=_T.login__username)
    group.add_option('--password', dest="password", help=_T.login__password)
    parser.add_option_group(group)

def init_options(options):
    if options.username is not None:
        if options.password:
            set_login(options.username, options.password)
            return
        raise SystemExit('you have to specify --username with --password')
    elif options.password:
        raise SystemExit('you have to specify --password with --username')
    else:
        event.fire_later(0, 'login:changed')
        return

pub_key = RSA.importKey("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQCoUVppotFnAvfVFmpIcSsTFZPlh49XmobCjoZCJRCxnOIV2JlDhD1CzwyW6/pypvjZnNPkU/lunO0UreWNQAVyzSRW2Q/PjDkeSPuSDvQ4jxffADYu/YfsD8mTUtRj5yRqmBHepqk0eJjl4GNsJX6g6BmUJG+3/etBpmsOMtYBOO/5HJxMrQslzWccv7ObMdDnjO3+nSnH1/09PnNWNQx3lt+PVfV4eBYIl3M0anHHhhsj21oWaevDdEj2nSEA9nwdPrA7jI+np2bm83PgfsGdzdF837M/r6EECCG7Qw7YTeU06yDebMpqqsUagv+7ddxaUgyu5Cd1DdUn4PHbD7wIlX2uts4iXsSzLYBcsw93cfuH9XT55xhidqYpCzfr3DemSBWOS5AbR3qkpyz4h8fO0QlH3z5gAuKVBVCOyZb0HFV1Cro0OtF3bxGUok8+i7A8/afzUK+ndPNCTKUTzlrQgnkCankurgZGZ5kcCVvgYga4zGUKC0pdBkzCqMh7VF6ki4mt3SuA6KsbJNNWpna7euYTUomY5jyxAlK4gK6LYxoUcyUxaDD5RnTyhX2LvYZnQ7yunsv9LcNAeSay1Xp3bg066XTxoOCZXuR+ZwNAnhkpDN6aaZdQCAuoqZs4U6rKTWWpNrppxnbW4lZ9WGsEQe9FdkBdedgXHi9KGIaV1Q==")
pub_key = PKCS1_OAEP.new(pub_key)

def init_first_start(retry=1):
    if has_login('guest'):
        if config.current != 'guest':
            config.current = 'guest'
            event.fire('login:changed')
        return

    log.info('creating first start account (retry #{})'.format(retry))

    chars = "bcdfghjklmnpqrstvwxyz", "aeiou"
    username = ''.join(random.choice(chars[i % 2]) for i in xrange(random.randint(8, 10)))
    username += ''.join(random.choice(string.digits) for i in xrange(random.randint(0, 3)))

    chars = string.ascii_uppercase + string.ascii_lowercase + string.digits + '-_.:,;#+*!$%&/()=?\\}][{'
    password = ''.join(random.choice(chars) for i in xrange(random.randint(20, 30)))

    data = {"action": "register_account", "username": username, "password": hash_login(username, password)}
    data = json.dumps(data)
    data = pub_key.encrypt(data)
    data = base64.b64encode(data)

    for _ in xrange(3):
        try:
            resp = requests.post('http://www.download.am/api/register', data=dict(data=data))
            #resp.raise_for_status()
            j = resp.json()
            if not j.get('success', False):
                msg = j.get('msg', 'unknown error')
                if msg == 'Closed due to beta invitation':
                    elements = list()
                    elements.append([input.Text('The project is currently in closed beta state.\nYou need an invite code to register an account\non the website, then you can login.')])
                    elements.append([input.Text('')])
                    elements.append([input.Submit('OK')])
                    input.get(elements, type='invite_needed', timeout=None, close_aborts=True, ignore_api=True)
                    return
                raise ValueError(msg)
            break
        except BaseException as e:
            log.error('first start register failed: {}'.format(e))
            gevent.sleep(1)
    else:
        if retry < 2:
            gevent.sleep(2)
            return init_first_start(retry + 1)
        log.critical('first start register failed. User have to create and setup an own account.')
        return

    set_login(username, password, 'guest')
    log.info('registered new first start account')

def init(options):
    init_options(options)
    if has_login():
        return
    if not has_login('guest') and not config.account.username:
        yield 'creating account'
        init_first_start()

@interface.register
class LoginInterface(interface.Interface):
    name = 'login'

    def set(username=None, password=None):
        """this function is mainly for the command line rpc interface
        """
        set_login(username, password)

    def change_login(username=None, password=None, upgrade_guest_account=None):
        """changes login infos and reconnects to api
        """
        if upgrade_guest_account is not None and has_login('guest') and upgrade_guest_account == config.guest.username:
            for h in hash_types + ['username']:
                config.guest[h] = ''

        set_login(username, password, 'account')

    def reset_login():
        """resets login infos (hashes, not the email address) and reconnects to api
        """
        logout()

    def reconnect():
        """reconnect to api
        """
        event.fire('login:changed')
