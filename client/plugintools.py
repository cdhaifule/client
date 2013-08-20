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

import os
import re
import sys
import json
import gevent
import urllib
import fnmatch
import requests
import urlparse
import functools
import traceback

from imp import new_module
from gevent.event import Event
from pkgutil import iter_modules
from importlib import import_module

from . import settings, logger, input, captcha, scheme, event

log = logger.get('plugintools')

################################## module functions


def iterplugins(type):
    package = 'client.plugins.{}'.format(type)
    for loader, name, ispkg in iter_modules([os.path.join(settings.script_dir, *package.split('.'))]):
        yield '.'+name, package

def itermodules(type, load_external=True):
    for name, package in iterplugins(type):
        try:
            log.debug('loading {}{}'.format(package, name))
            yield import_module(name, package)
        except ImportError:
            log.exception('missing some imports while loading module {}{}'.format(package, name))
        except:
            log.exception('error loading {}{}'.format(package, name))

    if not load_external or not os.path.exists(settings.external_plugins):
        raise StopIteration()

    from . import patch
    for extern in os.listdir(settings.external_plugins):
        path = os.path.join(settings.external_plugins, extern)
        if not os.path.isdir(path):
            continue

        files = patch.get_file_iterator(extern, type, False)

        try:
            files = list(files)
        except OSError:
            continue
        except:
            traceback.print_exc()
            continue

        path = os.path.join(path, type)
        extern_name = "client.plugins.{}.external_{}_".format(type, extern)
        extern_display_name = "client.plugins.{}.{}.".format(type, extern)

        for file in files:
            module_name, ext = os.path.splitext(file.name)
            if ext != ".py":
                continue

            name = "{}{}".format(extern_name, module_name)
            display_name = "{}{}".format(extern_display_name, module_name)

            log.debug("loading external {}".format(display_name))
            try:
                module = new_module(name)
                module.__file__ = os.path.join(path, file.name)
                code = compile(file.get_contents(), module.__file__, 'exec')
                exec code in module.__dict__
                sys.modules[name] = module
                yield module
            except ImportError:
                log.exception('missing some imports while loading external module {}'.format(display_name))
            except:
                log.exception('error loading external module {}'.format(display_name))

def load(type):
    return list(itermodules(type))
    
def auto_generate_filename(file):
    for field in ["name", "title"]:
        try:
            return file.pmatch[field]
        except KeyError:
            pass
    else:
        try:
            return "{} - ID: {}".format(file.host.name, file.pmatch["id"])
        except (KeyError, AttributeError):
            try:
                return os.path.split(file.split_url.path)[1]
            except:
                return file.url

################################## queue functions

class EmptyQueue(object):
    def __init__(self, *args, **kwargs):
        self.items = list()
        self.event = Event()
        self.event.set()

    def put(self, item):
        self.items.append(item)
        self.event.clear()

    def get(self):
        self.items.pop(0)
        if not self.items:
            self.event.set()

    def clear(self):
        self.items = list()
        self.event.set()

    def wait(self):
        self.event.wait()

    def __contains__(self, item):
        return item in self.items

################################## matching tools

RETYPE = type(re.compile(""))

def wildcard(wc):
    return regexp(fnmatch.translate(wc))

def regexp(reg):
    return re.compile(reg)
    
def between(a, l, t=None):
    start = a.index(l) + len(l)
    if t is None:
        end = None
    else:
        try:
            end = a.index(t, start)
        except IndexError:
            end = None
    return a[start:end]
    
def after(a, l):
    try:
        return between(a, l)
    except ValueError:
        return a

################################## some default error/input member functions

class ErrorFunctions(object):
    # base functions

    def _dhf_error(self, _msg, seconds=None, need_reconnect=False, msg=None, seperator=':'):
        msg = msg and '{}{} {}'.format(_msg, seperator, msg) or _msg
        if seconds:
            self.retry(msg, seconds, need_reconnect)
        else:
            if need_reconnect is True:
                raise RuntimeError('need_reconnect can only be handled when seconds is set')
            self.fatal(msg)
        return msg

    def _dhf_aborted(self, _msg, seconds=None, need_reconnect=False, msg=None):
        self._dhf_error('{} input aborted'.format(_msg), seconds, need_reconnect, msg)

    def _dhf_timeout(self, _msg, seconds=None, need_reconnect=False, msg=None):
        self._dhf_error('{} input timed out'.format(_msg), seconds, need_reconnect, msg)

    def _dhf_invalid(self, _msg, seconds=None, need_reconnect=False, msg=None):
        self._dhf_error('{} input failed'.format(_msg), seconds, need_reconnect, msg)

    # input errors

    def captcha_aborted(self, seconds=None, need_reconnect=False, msg=None):
        self._dhf_aborted('captcha', seconds, need_reconnect, msg)

    def captcha_timeout(self, seconds=None, need_reconnect=False, msg=None):
        self._dhf_timeout('captcha', seconds, need_reconnect, msg)

    def captcha_invalid(self, seconds=None, need_reconnect=False, msg=None):
        self._dhf_invalid('captcha', seconds, need_reconnect, msg)

    def password_aborted(self, seconds=None, need_reconnect=False, msg=None):
        self._dhf_aborted('password', seconds, need_reconnect, msg)

    def password_timeout(self, seconds=None, need_reconnect=False, msg=None):
        self._dhf_timeout('password', seconds, need_reconnect, msg)

    def password_invalid(self, seconds=None, need_reconnect=False, msg=None):
        self._dhf_invalid('password', seconds, need_reconnect, msg)

    def input_aborted(self, seconds=None, need_reconnect=False, msg=None):
        self._dhf_aborted('input', seconds, need_reconnect, msg)

    def input_timeout(self, seconds=None, need_reconnect=False, msg=None):
        self._dhf_timeout('input', seconds, need_reconnect, msg)

    def input_invalid(self, seconds=None, need_reconnect=False, msg=None):
        self._dhf_invalid('input', seconds, need_reconnect, msg)

    # check/download errors

    def no_more_free_traffic(self, seconds=None, need_reconnect=False, msg=None):
        ctx = self.account if seconds else self
        ctx._dhf_error('no more free traffic available', seconds, need_reconnect, msg)
    no_free_traffic = no_more_free_traffic

    def ip_blocked(self, seconds=None, need_reconnect=True, msg=None):
        ctx = self.account if seconds else self
        ctx._dhf_error('your ip is blocked', seconds, need_reconnect, msg)

    def premium_needed(self, seconds=None, need_reconnect=False, msg=None):
        self._dhf_error('you need a premium account to download this file', seconds, need_reconnect, msg)

    def no_download_link(self, seconds=None, need_reconnect=False, msg=None):
        self._dhf_error('error getting download link', seconds, need_reconnect, msg)
    no_download_url = no_download_link

    def only_one_connection_allowed(self, seconds=None, need_reconnect=False, msg=None):
        self._dhf_error('your ip is already downloading a file', seconds, need_reconnect, msg)

    def temporary_unavailable(self, seconds=None, need_reconnect=False, msg=None):
        self._dhf_error('temporary unavailable', seconds, need_reconnect, msg)

    def maintenance(self, seconds=None, need_reconnect=False, msg=None):
        self._dhf_error('server is in maintenance mode', seconds, need_reconnect, msg)

    # plugin out of date errors. errors could be reported to backend

    def plugin_out_of_date(self, seconds=None, need_reconnect=False, msg=None, content=None, backend_report=True):
        if backend_report:
            gevent.spawn(log.send, 'warning', msg, content)
        self.log.exception('plugin_out_of_date')
        self._dhf_error('plugin out of date', seconds, need_reconnect, msg, '?')

    def parse_error(self, what, seconds=None, need_reconnect=False, content=None, backend_report=True):
        self.plugin_out_of_date(seconds, need_reconnect, 'error parsing {}'.format(what), content, backend_report)

    def unhandled_exception(self):
        exc = sys.exc_info()
        msg = traceback.format_exception_only(exc[0], exc[1])[0].strip()
        self.plugin_out_of_date(1800, False, msg, exc, True)

class InputFunctions(object):
    def get_input(self, type, seconds=None, **kwargs):
        """see input.input_loop for kwargs arguments.
        seconds - only used with InputTimeout exception
        """
        kwargs['parent'] = self
        try:
            return input.input_loop(**kwargs)
        except input.InputAborted as e:
            getattr(self, '{}_aborted'.format(type))(msg=str(e) or None)
        except input.InputTimeout as e:
            getattr(self, '{}_timeout'.format(type))(msg=str(e) or None, seconds=seconds)
        except input.InputFailed as e:
            getattr(self, '{}_invalid'.format(type))(msg=str(e) or None)
    
    def iter_input(self, type, seconds=None, **kwargs):
        kwargs['parent'] = self
        try:
            for result in input.input_iter(**kwargs):
                yield result
        except input.InputAborted as e:
            getattr(self, '{}_aborted'.format(type))(msg=str(e) or None)
        except input.InputTimeout as e:
            getattr(self, '{}_timeout'.format(type))(msg=str(e) or None, seconds=seconds)
        except input.InputFailed as e:
            getattr(self, '{}_invalid'.format(type))(msg=str(e) or None)


    def input_remember_boolean(self, text, seconds=None, **kwargs):
        try:
            elements = list()
            elements.append(input.Text(text))
            elements.append(input.Input('remember', 'checkbox', default=False, label='Remember decision?'))
            elements.append(input.Choice('answer', choices=[{"value": "true", "content": "Yes"}, {"value": "false", "content": "No"}]))
            result = input.get(elements, type='remember_boolean', parent=self, **kwargs)
            return result.get('remember', False), result.get("answer", "false") == "true"
        except input.InputAborted:
            return None, None
        except input.InputTimeout:
            return None, None

    def input_remember_boolean_config(self, config, key, text, seconds=None, **kwargs):
        if config[key] is not None:
            return config[key]
        remember, result = self.input_remember_boolean(text, seconds=seconds, **kwargs)
        if remember is not None:
            with scheme.transaction:
                config[key] = result
        return result

    def input_remember_button(self, text, seconds=None, **kwargs):
        try:
            elements = list()
            elements.append(input.Text(text))
            elements.append(input.Choice('answer', choices=[
                {"value": "yes", "content": "Yes"},
                {"value": "no", "content": "No"},
                {"value": "never", "content": "No, and don't ask again"}]))
            result = input.get(elements, type='remember_boolean', parent=self, **kwargs)
            return result.get("answer", "") == "never", result.get("answer", "false") == "yes"
        except input.InputAborted:
            return None, None
        except input.InputTimeout:
            return None, None


    def solve_password(self, seconds=None, **kwargs):
        return self.iter_input('password', func=input.password, seconds=seconds, **kwargs)
    
    def solve_password_www(self, seconds=None, **kwargs):
        return self.iter_input('password', func=input.password_www, seconds=seconds, **kwargs)

    def solve_captcha(self, module=None, seconds=None, browser=None, retries=5, **kwargs):
        """see input.input_loop for kwargs arguments.
        seconds - only used with InputTimeout exception
        testfunc - testfunc(result, challenge)
        """
        if module is None:
            if not kwargs.get('func', None):
                kwargs['func'] = input.captcha_text
        else:
            module = captcha.__dict__[module]
            kwargs['func'] = module.solve

        if 'parse' in kwargs:
            kwargs['challenge_id'] = module.parse(kwargs['parse'])
            del kwargs['parse']

        return self.iter_input('captcha', seconds=seconds, retries=retries, browser=browser or self.account, **kwargs)

    solve_captcha_text = solve_captcha

    def solve_captcha_image(self, **kwargs):
        kwargs['func'] = input.captcha_image
        return self.solve_captcha(**kwargs)

################################## default greenlet object

class GreenletObject(object):
    def __init__(self):
        self._greenlet_events = list()
        self._greenlet_funcs = list()

    def _spawn(self, spawn, *args, **kwargs):
        def run(func, *args, **kwargs):
            try:
                self.on_greenlet_started()
                return func(*args, **kwargs)
            finally:
                with scheme.transaction:
                    self.greenlet = None
                self.on_greenlet_finish()
                while self._greenlet_funcs:
                    func, args, kwargs = self._greenlet_funcs.pop(0)
                    func(*args, **kwargs)
                while self._greenlet_events:
                    e, args, kwargs = self._greenlet_events.pop(0)
                    event.fire(e, *args, **kwargs)
                self.on_greenlet_stopped()
        with scheme.transaction:
            self.greenlet = spawn(run, *args, **kwargs)
        return self.greenlet

    def on_greenlet_started(self):
        pass

    def on_greenlet_finish(self):
        pass

    def on_greenlet_stopped(self):
        pass

    def spawn(self, func, *args, **kwargs):
        return self._spawn(gevent.spawn, func, *args, **kwargs)

    def spawn_later(self, seconds, func, *args, **kwargs):
        return self._spawn(gevent.spawn_later, seconds, func, *args, **kwargs)

    def join(self):
        if self.greenlet:
            self.greenlet.join()

    def kill(self):
        if self.greenlet:
            self.greenlet.kill()

    def run_after_greenlet(self, func, *args, **kwargs):
        if self.greenlet is None:
            gevent.spawn(func, *args, **kwargs)
        else:
            self._greenlet_funcs.append((func, args, kwargs))

    def fire_after_greenlet(self, e, *args, **kwargs):
        if self.greenlet is None:
            event.fire(e, *args, **kwargs)
        else:
            self._greenlet_events.append((e, args, kwargs))


class FakeGreenlet(object):
    def __init__(self, obj):
        self.obj = obj
        obj.on_greenlet_started()

    def kill(self):
        self.obj.on_greenlet_stopped()
        with scheme.transaction:
            self.obj.greenlet = None


################################## default exception catch function

class NoMoreConnectionsError(BaseException):
    pass

class RangeNotSatisfiedError(BaseException):
    pass

def ctx_error_handler(ctx, func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except requests.ConnectionError:
        ctx.retry('connection error', 60)
    except requests.HTTPError:
        ctx.retry('HTTP error', 60)
    except requests.TooManyRedirects:
        ctx.retry('HTTP error', 180)
    except (KeyboardInterrupt, SystemExit, gevent.GreenletExit):
        raise
    except NoMoreConnectionsError:
        raise
    except RangeNotSatisfiedError:
        raise
    except:
        try:
            ctx.unhandled_exception()
        except gevent.GreenletExit:
            pass
        raise

################################## url parse and match functions

class AllUrlRegex(object):
    regex = r"(?P<url>(?P<scheme>\w[\w\d\-]+?):(?P<sub1>((?P<scheme_delim>//)((?P<username>[^\@\/\s\"\'\:]*)(:(?P<password>[^\@\/\s\"\']*))?@)?(?P<host>[^\?\/\:\s\"\']*)(:(?P<port>\d+))?(?P<path>/[^\s\?\#\"\']*)?)?)(?P<sub2>(\?(?P<query>[^\s\#\"\']*))?(\#(?P<fragment>[^\s\"\']*))?))\s*"
    compiled = re.compile(regex)

    def match(self, text):
        try:
            m = self.compiled.match(text)
            if m.group('sub1') or m.group('sub2'):
                return m
        except AttributeError:
            log.error("'{}' is not a url".format(text))
            
    def finditer(self, text):
        for m in self.compiled.finditer(text):
            if m.group('sub1') or m.group('sub2'):
                yield m

    def __str__(self):
        return self.regex
    __unicode__ = __str__

all_url_regex = AllUrlRegex()

class Url(object):
    url = None
    scheme = None
    scheme_delim = None
    username = None
    password = None
    host = None
    port = None
    path = None
    path_prefix = ''
    query = None
    fragment = None

    def __init__(self, url):
        """query_string is ignored on to_string function
        """
        self.original_url = url
        m = all_url_regex.match(url)
        if m is None:
            raise ValueError('failed matching url {}'.format(url))
        for key, value in m.groupdict().iteritems():
            setattr(self, key, value)
        # win32 path fix
        if self.path is not None and re.match('^/[a-zA-Z]:\\\\', self.path):
            self.path = self.path[1:]
            self.path_prefix = '/'
        if self.query is not None:
            self.query_string = self.query
            self.query = {k: v[0] for k, v in urlparse.parse_qs(self.query, True).iteritems()}
        else:
            self.query_string = None
            self.query = dict()
        
    def to_string(self):
        result = ''
        if self.scheme is not None:
            result += self.scheme + ':' + (self.scheme_delim or '')
        if self.username is not None or self.password is not None:
            result += (self.username or '') + (self.password and (':' + self.password) or '') + '@'
        if self.host is not None:
            result += self.host
            if self.port is not None:
                result += ':' + str(self.port)
        if self.path is not None:
            result += urllib.quote(self.path_prefix+self.path)
        if self.query:
            result += '?' + urllib.urlencode(self.query)
        if self.fragment is not None:
            result += '#' + urllib.quote_plus(self.fragment)
        return result

    def __str__(self):
        return self.to_string()

class MatchContext(dict):
    def __init__(self, matcher):
        self.matcher = matcher

    def __getattr__(self, key):
        return self[key]

    def __setattr__(self, key, value):
        self[key] = value

    def __delattr__(self, key):
        del self[key]

class Matcher(object):
    def __init__(self, _scheme=None, _host=None, _path=None, _query_string=None, **query):
        """query parameter usage:
            fileid="id"
                value of fileid will be saved to variable id.
            filename="|name"
                optional parameter filename (note the |). value will be saved to name.
                if the parameter not exists, the value of name is set to None.
        strings starting with:
            ~ or any other character (except of ! and *) are interpreted as regex
            ! are interpreted as bottle syntax
            * are interpreted as wildcard (*. at the beginning is transformed to (.*\.)? regex)
        """
        self.scheme = self._compile(_scheme)
        self.host = self._compile(_host)
        self.path = self._compile(_path)
        self.query_string = self._compile(_query_string)
        self.query = dict()
        self.tag = "download"
        self.opt_query = dict()
        self.match_query = dict()
        for key, value in query.iteritems():
            if isinstance(value, tuple):
                self.match_query[key] = (value[0], self._compile(value[1])[0])
            elif value.startswith('|'):
                self.opt_query[key] = value[1:]
            else:
                self.query[key] = value
        self.template = None
    
    def set_tag(self, tag):
        self.tag = tag
        return self

    def set_template(self, template):
        self.template = template
        return self

    def _compile(self, p):
        if p is not None:
            def c(p):
                if p.startswith('*'):
                    if p[:2] == '*.':
                        p = p[2:]
                        q = True
                    else:
                        q = False
                    return re.compile((q and '(.*\.)?' or '') + fnmatch.translate(p))
                if p.startswith('!'):
                    return bottle_compile(p[1:])
                if p.startswith('~'):
                    p = p[1:]
                if p.startswith('='):
                    class m(object):
                        def match(self, other):
                            return p[1:] == other
                    return m()
                return re.compile(p)
            p = map(c, isinstance(p, list) and p or [p])
        return p

    def match(self, url):
        ctx = MatchContext(self)
        ctx.tag = self.tag
        for part in ('scheme', 'host', 'path', 'query_string'):
            if not self._match(ctx, url, part):
                return
        for key, (alias, match) in self.match_query.iteritems():
            if key not in url.query:
                return
            else:
                if not match.match(url.query[key]):
                    return
        for key, alias in self.query.iteritems():
            if key not in url.query:
                return
            ctx[alias] = url.query[key]
        if self.opt_query:
            for key, alias in self.opt_query.iteritems():
                ctx[alias] = url.query.get(key, None)
        return ctx

    def _match(self, ctx, url, key):
        matches = getattr(self, key)
        if matches is None:
            return True
        value = getattr(url, key)
        if value is None:
            return False
        for match in matches:
            m = match.match(value)
            if m:
                ctx[key] = value
                for k, v in m.groupdict().iteritems():
                    ctx[k] = v
                return True
        return False

    def _decompile(self, pattern):
        if pattern is None:
            return ''
        if isinstance(pattern, list):
            pattern = '|'.join(self._decompile(p) for p in pattern)
            return '('+pattern+')'
        return pattern.pattern.lstrip('^').rstrip('$').replace('\Z(?ms)', '')

    def get_regex(self, with_scheme=True):
        if hasattr(self, '_decompiled_regex'):
            return self._decompiled_regex
        r = ''
        if with_scheme:
            r += '(?P<url>'
            p = self._decompile(self.scheme)
            if p:
                r += p
            r += '://'
        else:
            if not (self.host or self.path or self.query or self.opt_query):
                return
            r += '[^\w\d]?(?P<url>'

        r += self._decompile(self.host)
        r += self._decompile(self.path)

        if self.query:
            keys = '|'.join(re.escape(k) for k in self.query.values())
            r += '([\?&]('+keys+')=[^&#\?]*)+'

        if self.opt_query:
            keys = '|'.join(re.escape(k) for k in self.opt_query.values())
            r += '([\?&]('+keys+')=[^&#\?]*)*'

        r += '[^\s<>]*)'
        self._decompiled_regex = re.compile(r)
        return self._decompiled_regex

################################## bottle url match syntax
# stolen from https://github.com/defnull/bottle/blob/master/bottle.py line 285

_bottle_rule_syntax = re.compile('(\\\\*)(?:(?::([a-zA-Z_][a-zA-Z_0-9]*)?()(?:#(.*?)#)?)|(?:<([a-zA-Z_][a-zA-Z_0-9]*)?(?::([a-zA-Z_]*)(?::((?:\\\\.|[^\\\\>]+)+)?)?)?>))')

_bottle_filters = {
    're': lambda conf: _bottle_re_flatten(conf or r'[^/]+'),
    'int': lambda conf: r'-?\d+',
    'float': lambda conf: r'-?\d(\.\d+)?+',
    'path': lambda conf: r'.+?'}

def _bottle_re_flatten(p):
    if '(' in p:
        return re.sub(r'(\\*)(\(\?P<[^>]*>|\((?!\?))',
            lambda m: m.group(0) if len(m.group(1)) % 2 else m.group(1) + '(?:', p)
    return p

def _bottle_itertokens(rule):
    offset, prefix = 0, ''
    for match in _bottle_rule_syntax.finditer(rule):
        prefix += rule[offset:match.start()]
        g = match.groups()
        if len(g[0])%2: # Escaped wildcard
            prefix += match.group(0)[len(g[0]):]
            offset = match.end()
            continue
        if prefix:
            yield prefix, None, None
        name, filtr, conf = g[4:7] if g[2] is None else g[1:4]
        yield name, filtr or 'default', conf or None
        offset, prefix = match.end(), ''
    if offset <= len(rule) or prefix:
        yield prefix+rule[offset:], None, None

def bottle_compile(rule):
    anons = 0
    pattern = ''
    for key, mode, conf in _bottle_itertokens(rule):
        if mode:
            if mode == 'default':
                mode = 're'
            mask = _bottle_filters[mode](conf)
            if not key:
                pattern += '(?:%s)' % mask
                key = 'anon%d' % anons
            else:
                pattern += '(?P<%s>%s)' % (key, mask)
        elif key:
            pattern += re.escape(key)
    pattern = '^%s' % pattern
    pattern = re.compile(pattern)
    return pattern


###################### function to get data out of the database

def dict_json(d):
    for k, v in d.iteritems():
        if v != "" and v is not None and not type(v) in (int, long, float):
            try:
                try:
                    d[k] = json.loads(v)
                except:
                    if not re.match('^\d+L$', v):
                        raise
                    d[k] = json.loads(v[:-1])
            except BaseException as e:
                log.error("error loading row: {}: {}/{}".format(e, k, v, e))
                raise RuntimeError("Broken row...{}".format(d))


###################### decorator to convert return value to filesystem encoding

_filesystem_encoding = sys.getfilesystemencoding()

def filesystemencoding(f):
    if sys.platform == 'win32':
        @functools.wraps(f)
        def _enc(*args, **kwargs):
            x = f(*args, **kwargs)
            if x[1] == ':':
                x = x[:2] + x[2:].replace(':', '_')
            else:
                x = x.replace(':', '_')
            if isinstance(x, unicode):
                x = x.encode(_filesystem_encoding)
            return x
    else:
        @functools.wraps(f)
        def _enc(*args, **kwargs):
            x = f(*args, **kwargs)
            if isinstance(x, unicode):
                x = x.encode(_filesystem_encoding)
            return x
    return _enc
