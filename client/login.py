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

log = logger.get("login")

hash_types = ['login', 'frontend', 'backend', 'client', 'protected', 'gasecret']

config = globalconfig.new('login')
config.default('first_start', None, dict, private=True)

config.default('username', "", unicode, private=True)
config.default('save_password', True, bool, private=True)

hashes = dict()
for h in hash_types:
    hashes[h] = None
    config.hashes.default(h, None, str, private=True)

_config_loaded = False

is_guest = False

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

def hash_gasecret(*args):
    return sha256("".join(args))[:16].upper()

def set_login(username, password, save_password=True):
    global _config_loaded, is_guest
    is_guest = False
    _config_loaded = True

    with transaction:
        config['username'] = username
        if save_password is not None and save_password:
            config['save_password'] = save_password

        if username is not None:
            hashes['login'] = hash_login(username, password)
            hashes['frontend'] = hash_frontend(username, password, hashes['login'])
            hashes['backend'] = None
            hashes['protected'] = hash_protected(password, hashes['frontend'])
            hashes['client'] = hash_client(hashes['login'], hashes['frontend'])
            hashes['gasecret'] = hash_gasecret(hashes['login'], hashes['frontend'], hashes['protected'])

            if save_password:
                for h in hash_types:
                    config.hashes[h] = hashes[h]

        if config['username'] is None or not save_password:
            for h in hash_types:
                config.hashes[h] = None

    event.fire('login:changed')
    
def set_guest_state(value):
    global is_guest
    is_guest = bool(value)

def generate_backend_key():
    if not has_login():
        raise RuntimeError('setting backend key while no login is set')
    key = hashes.get("backend")
    if not key:
        key = sha256(Random.new().read(32))
        hashes['backend'] = key
    return key
    
def get_sso_url(tab=None):
    return "http://{}/#sso!{}".format(settings.frontend_domain, get_auth_token(tab))
    
def logout():
    if config.first_start is not None:
        for h in hash_types:
            if h != 'backend' and config.first_start[h] != hashes[h]:
                break
        else:
            config.username = ''

    for h in hash_types:
        hashes[h] = None

    if config['save_password']:
        for h in hash_types:
            config.hashes[h] = None

    event.fire('login:changed')

# event is set when login data is present
login_event = Event()

def has_login():
    if config['username'] is None:
        return False
    for h in hash_types:
        if h != 'backend' and not hashes[h]:
            return False
    return True

def wait():
    login_event.wait()

def get(type):
    wait()
    return hashes[type]

def get_login():
    return get('login')

def get_auth_token(tab=None):
    """returns base64 encoded auto token for automatic login to website
    """
    char_set = string.ascii_uppercase + string.ascii_lowercase + string.digits
    key = ''.join(random.sample(char_set*12, 12))

    data = "{};{};{};{}".format(config['username'], get('login'), get('frontend'), settings.app_uuid)
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
            #if source == "backend":
            #    sys.exit(1)
            #else:
            #    raise
    else:
        return data

@event.register('config:before_load')
def on_config_before_load(e, data):
    if 'first_start' in data and isinstance(data['first_start'], basestring):
        data['first_start'] = json.loads(data['first_start'])

@event.register('config:loaded')
def on_config_loaded(e):
    global _config_loaded
    if _config_loaded:
        return
    _config_loaded = True

    for h in hash_types:
        try:
            hashes[h] = config.hashes[h]
        except (AttributeError, KeyError):
            hashes[h] = None

login_input = None

@event.register('login:changed')
def on_login_changed(e):
    global login_input

    module_initialized.wait()
    ui.module_initialized.wait()
    
    if has_login():
        print "have login infos"
        login_event.set()
        if login_input is not None:
            login_input.kill()
            login_input = None
    else:
        print "login infos missing"
        if login_input is not None:
            print "login input active"
            return
        if not ui.ui.has_ui:
            log.error("Cannot login without user interface. For commandline usage see --help.")
            sys.exit(1)
        login_event.clear()
        elements = list()
        elements.append([input.Text('Please input your login informations')])
        elements.append([input.Float('left')])
        elements.append([input.Text('')])
        elements.append([input.Text('E-Mail:'), input.Input('username', value=config.username)])
        elements.append([input.Text('Password:'), input.Input('password', 'password')])
        elements.append([input.Float('right')])
        elements.append([input.Text('')])
        sub = input.Subbox()
        sub.elements.append([input.Float('left')])
        sub.elements.append([input.Link('http://{}/#pwlose'.format(settings.frontend_domain), 'Forgot password?')])
        sub.elements.append([input.Link('http://{}/#register'.format(settings.frontend_domain), 'Register')])
        elements.append([sub, input.Input('save_password', 'checkbox', default=config.save_password, label='Save password')])
        elements.append([input.Choice('action', choices=[
            dict(value='ok', content='OK', ok=True),
            dict(value='cancel', content='Cancel', cancel=True),
            dict(value='guest', content='Connect as guest')])])

        def _login_input():
            try:
                result = input.get(elements, type='login', timeout=None, close_aborts=True, ignore_api=True)
            except input.InputAborted:
                sys.exit(1)
            else:
                if result['action'] == 'cancel':
                    sys.exit(1)
                elif result['action'] == 'guest':
                    init_first_start(1, result.get('save_password', False))
                else:
                    set_login(result['username'], result['password'], result.get('save_password', False))
        login_input = gevent.spawn(_login_input)

def init_optparser(parser, OptionGroup):
    group = OptionGroup(parser, _T.login__options)
    group.add_option('--username', dest="username", help=_T.login__username)
    group.add_option('--password', dest="password", help=_T.login__password)
    group.add_option('--save-password', dest="save_password", action="store_true", default=False, help=_T.login__save_password)
    parser.add_option_group(group)

def init_options(options):
    if options.username is not None:
        if options.password:
            set_login(options.username, options.password, options.save_password)
            return
        raise SystemExit('you have to specify --username with --password')
    elif options.password:
        raise SystemExit('you have to specify --password with --username')
    elif options.save_password:
        raise SystemExit('you have to specify --username and --password with --save-password')
    else:
        event.fire_later(0, 'login:changed')
        return

pub_key = RSA.importKey("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQCoUVppotFnAvfVFmpIcSsTFZPlh49XmobCjoZCJRCxnOIV2JlDhD1CzwyW6/pypvjZnNPkU/lunO0UreWNQAVyzSRW2Q/PjDkeSPuSDvQ4jxffADYu/YfsD8mTUtRj5yRqmBHepqk0eJjl4GNsJX6g6BmUJG+3/etBpmsOMtYBOO/5HJxMrQslzWccv7ObMdDnjO3+nSnH1/09PnNWNQx3lt+PVfV4eBYIl3M0anHHhhsj21oWaevDdEj2nSEA9nwdPrA7jI+np2bm83PgfsGdzdF837M/r6EECCG7Qw7YTeU06yDebMpqqsUagv+7ddxaUgyu5Cd1DdUn4PHbD7wIlX2uts4iXsSzLYBcsw93cfuH9XT55xhidqYpCzfr3DemSBWOS5AbR3qkpyz4h8fO0QlH3z5gAuKVBVCOyZb0HFV1Cro0OtF3bxGUok8+i7A8/afzUK+ndPNCTKUTzlrQgnkCankurgZGZ5kcCVvgYga4zGUKC0pdBkzCqMh7VF6ki4mt3SuA6KsbJNNWpna7euYTUomY5jyxAlK4gK6LYxoUcyUxaDD5RnTyhX2LvYZnQ7yunsv9LcNAeSay1Xp3bg066XTxoOCZXuR+ZwNAnhkpDN6aaZdQCAuoqZs4U6rKTWWpNrppxnbW4lZ9WGsEQe9FdkBdedgXHi9KGIaV1Q==")
pub_key = PKCS1_OAEP.new(pub_key)

def init_first_start(retry=1, save_password=True):
    print "!"*100, config.first_start
    if config.first_start is not None:
        config.username = config.first_start['username']
        for key in hash_types:
            hashes[key] = config.first_start[key]
            if save_password:
                config.hashes[key] = config.first_start[key]
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

    set_login(username, password, True)
    with transaction:
        config.first_start = hashes
        config.first_start['username'] = username

    log.info('registered new first start account')

def init(options):
    init_options(options)
    if has_login():
        return
    if config.first_start is None and not config.username:
        yield 'creating account'
        init_first_start()

@interface.register
class LoginInterface(interface.Interface):
    name = 'login'

    def set(username=None, password=None, save_password=True):
        """this function is mainly for the command line rpc interface
        """
        set_login(username, password, save_password)

    def change_password(username=None, login=None, frontend=None, backend=None, protected=None, upgrade_guest_account=None):
        """changes login infos and reconnects to api
        """
        if upgrade_guest_account is not None and config.first_start is not None:
            print "!"*100, 'upgrade_guest_account', upgrade_guest_account
            print "!"*100, 'config.first_start', config.first_start
            if upgrade_guest_account == config.first_start['frontend']:
                config.first_start = None

        config['username'] = username

        hashes['login'] = login
        hashes['frontend'] = frontend
        hashes['backend'] = backend
        hashes['protected'] = protected
        hashes['client'] = hash_client(hashes['login'], hashes['frontend'])
        hashes['gasecret'] = hash_gasecret(hashes['login'], hashes['frontend'], hashes['protected'])

        if config['save_password']:
            for h in hash_types:
                config.hashes[h] = hashes[h]

        event.fire('login:changed')

    def reset_login():
        """resets login infos (hashes, not the email address) and reconnects to api
        """
        logout()

    def reconnect():
        """reconnect to api
        """
        event.fire('login:changed')
