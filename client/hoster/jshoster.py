import re
import os
import sys
import inspect
import traceback

from types import GeneratorType, NoneType, BooleanType, IntType, LongType, FloatType, ComplexType, StringType, UnicodeType, ListType, TupleType, DictType
from functools import partial

from . import host, util, Matcher, cfg, urljoin
from . import models as hoster_models
from ..javascript import PyV8


def secure_object(obj, names=None, slots=None):
    if names is None:
        names = [name for name in dir(obj) if not name.startswith('_')]
    elif isinstance(names, basestring):
        names = filter(lambda a: a, names.split(','))
    
    if isinstance(slots, basestring):
        slots = filter(lambda a: a, slots.split(','))

    class SecureModule(object):
        __slots__ = names + (slots or list())

    s = SecureModule()
    for name in s.__slots__:
        try:
            value = getattr(obj, name)
        except AttributeError:
            pass
        setattr(s, name, value)
    return s


def decorate_js_function(func):
    ctx = get_js_context()

    def fn(*args):
        with JSContext(ctx.world) as jsctx:
            jsctx = jsctx # foo
            args = list(args)
            for i, value in enumerate(args):
                args[i] = proxy_to_js(value)
            return func(*args)
    return fn

def proxy_from_js(original_obj):
    if isinstance(original_obj, ToJS):
        ctx = get_js_context()
        return proxy_from_js(ctx.local_objects[original_obj])
    elif isinstance(original_obj, (NoneType, BooleanType, IntType, LongType, FloatType, ComplexType, StringType, UnicodeType)):
        return original_obj
    elif isinstance(original_obj, PyV8.JSFunction):
        return decorate_js_function(original_obj)
    elif isinstance(original_obj, PyV8.JSArray):
        return [proxy_from_js(x) for x in list(original_obj)]
    elif isinstance(original_obj, PyV8.JSObject):
        obj = dict()
        for key in original_obj.keys():
            obj[key] = proxy_from_js(original_obj[key])
        return obj
    else:
        return original_obj


def get_js_context(start_frame=1):
    i = start_frame
    while True:
        try:
            frame = sys._getframe(i)
        except ValueError:
            break
        if 'jsctx' in frame.f_locals and isinstance(frame.f_locals['jsctx'], JSContext):
            return frame.f_locals['jsctx']
        i += 1
    raise RuntimeError("found no javascript context")

class ToJS(object):
    pass


def call_from_js_decorator(func):
    def fn(*args):
        try:
            args = list(args)
            for i, value in enumerate(args):
                args[i] = proxy_from_js(value)
            result = func(*args)
            return proxy_to_js(result)
        except AttributeError:
            raise
        except:
            traceback.print_exc()
            raise
    return fn

def convert_keys(keys):
    if keys is None:
        return dict()
    if isinstance(keys, basestring):
        return keys.split(',')
    return keys

def decorate_to_js(original_obj, attrs=None, ro_attrs=None, allow_getitem=False, allow_setitem=False, allow_delitem=False, allow_getattr=False, allow_setattr=False, allow_delattr=False):
    attrs = set(convert_keys(attrs))
    ro_attrs = set(convert_keys(ro_attrs))
    for c in [original_obj.__class__] + list(original_obj.__class__.__mro__):
        try:
            attrs |= set(c.__sb_attrs__)
        except AttributeError:
            pass
        try:
            ro_attrs |= set(c.__sb_ro_attrs__)
        except AttributeError:
            pass

    class _ToJS(ToJS):
        __doc__ = original_obj.__doc__
        __watchpoints__ = dict()

        if hasattr(original_obj, '__getitem__'):
            @call_from_js_decorator
            def __getitem__(self, key):
                if not allow_getitem:
                    raise RuntimeError('getitem is forbidden (requested key "{}" of object {})'.format(key, type(original_obj)))
                value = original_obj[key]
                return proxy_to_js(value)

        if hasattr(original_obj, '__setitem__'):
            @call_from_js_decorator
            def __setitem__(self, key, value):
                if not allow_setitem:
                    raise RuntimeError('setitem is forbidden (requested key "{}" of object {})'.format(key, type(original_obj)))
                original_obj[key] = value

        if hasattr(original_obj, '__delitem__'):
            @call_from_js_decorator
            def __delitem__(self, key):
                if not allow_delitem:
                    raise RuntimeError('delitem is forbidden (requested key "{}" of object {})'.format(key, type(original_obj)))
                del original_obj[key]

        @call_from_js_decorator
        def __getattr__(self, key):
            value = getattr(original_obj, key)
            if allow_getattr is False:
                a = attrs | set(original_obj.__sb_attrs__ if hasattr(original_obj, '__sb_attrs__') else [])
                roa = ro_attrs | set(original_obj.__sb_ro_attrs__ if hasattr(original_obj, '__sb_ro_attrs__') else [])
                if key not in a and key not in roa:
                    if not hasattr(value, '__sbfunc__') or not value.__sbfunc__:
                        raise RuntimeError('attribute "{}" of object {} is private'.format(key, type(original_obj)))
            return proxy_to_js(value)

        @call_from_js_decorator
        def __setattr__(self, key, value):
            if allow_setattr is False:
                roa = ro_attrs | set(original_obj.__sb_ro_attrs__ if hasattr(original_obj, '__sb_ro_attrs__') else [])
                if key in roa:
                    raise RuntimeError('attribute "{}" of object {} is read only'.format(key, original_obj))
                a = attrs | set(original_obj.__sb_attrs__ if hasattr(original_obj, '__sb_attrs__') else [])
                if key not in a:
                    raise RuntimeError('attribute "{}" of object {} is private'.format(key, original_obj))
            setattr(original_obj, key, value)

        @call_from_js_decorator
        def __delattr__(self, key, value):
            if allow_delattr is False:
                roa = ro_attrs | set(original_obj.__sb_ro_attrs__ if hasattr(original_obj, '__sb_ro_attrs__') else [])
                if key in roa:
                    raise RuntimeError('attribute "{}" of object {} is read only'.format(key, original_obj))
                a = attrs | set(original_obj.__sb_attrs__ if hasattr(original_obj, '__sb_attrs__') else [])
                if key not in a:
                    raise RuntimeError('attribute "{}" of object {} is private'.format(key, original_obj))
            delattr(original_obj, key, value)

        if hasattr(original_obj, '__iter__'):
            @call_from_js_decorator
            def __iter__(self):
                for value in original_obj:
                    yield proxy_to_js(value)

        if hasattr(original_obj, '__len__'):
            def __len__(self):
                return len(original_obj)

        if callable(original_obj):
            @call_from_js_decorator
            def __call__(self, *args):
                # convert arguments to python objects
                args = list(args)

                # get function argument specification
                argspec = inspect.getargspec(original_obj)

                #if argspec.varargs is not None:
                #    raise RuntimeError('functions with variable length arguments are not allowed')
                argnames = argspec.args
                if argspec.defaults:
                    argnames = argnames[:-len(argspec.defaults)]
                if hasattr(original_obj, 'im_self'):
                    argnames = argnames[1:]

                # get keyword arguments
                if len(args) == len(argnames) + 1:
                    kwargs = args.pop(-1)
                elif len(args) > len(argnames):
                    raise RuntimeError('too many arguments for function {}'.format(original_obj))
                else:
                    kwargs = dict()

                # put positional arguments to keyword arguments
                try:
                    arguments = inspect.getcallargs(original_obj, *args, **kwargs)
                except TypeError:
                    print "FUNCTION:", original_obj
                    print "ARGSPEC:", argspec
                    print "POSITIONAL ARGIMENTS:", argnames
                    raise
                if hasattr(original_obj, 'im_self'):
                    arguments.pop('self')
                if 'args' in arguments:
                    for i, value in enumerate(arguments['args']):
                        arguments[argnames[i]] = value
                    del arguments['args']
                
                # merge kwargs to main arguments
                try:
                    kwargs = arguments.pop('kwargs')
                    arguments.update(kwargs)
                except KeyError:
                    pass

                return original_obj(**arguments)

    for key in ('__repr__', '__str__', '__sizeof__', '__ne__', '__lt__', '__le__', '__gt__', '__ge__', '__eq__', '__contains__', '__nonzero__', '__neg__'):
        try:
            value = getattr(original_obj.__class__, key)
        except AttributeError:
            pass
        else:
            setattr(_ToJS, key, value)

    _ToJS.__module__ = str(original_obj.__module__) if hasattr(original_obj, '__module__') else str(_ToJS.__module__)

    obj = _ToJS()

    jsctx = get_js_context()
    jsctx.local_objects[obj] = original_obj

    return obj


import bs4
from requests.models import Response
from ..account import Account
from ..core import File, Chunk
from .. import plugintools

to_js_converters = list()
to_js_converters.append((
    (NoneType, BooleanType, IntType, LongType, FloatType, ComplexType, StringType, UnicodeType, ToJS),
    lambda original_obj: original_obj))

to_js_converters.append((
    GeneratorType,
    lambda original_obj: original_obj.next))

to_js_converters.append((
    Account,
    partial(
        decorate_to_js,
        ro_attrs='get,post')))

to_js_converters.append((
    (File, Chunk),
    partial(
        decorate_to_js,
        ro_attrs='set_infos,account,url,set_offline')))

to_js_converters.append((
    plugintools.MatchContext,
    decorate_to_js))

to_js_converters.append((
    Response,
    partial(
        decorate_to_js,
        ro_attrs='status_code,text,content,json,soup,url,get,post')))

to_js_converters.append((
    bs4.BeautifulSoup,
    partial(
        decorate_to_js,
        ro_attrs='select,find')))

to_js_converters.append((
    bs4.element.Tag,
    partial(
        decorate_to_js,
        ro_attrs='find_parent,get,text')))

to_js_converters.append((
    (ListType, TupleType, DictType),
    partial(
        decorate_to_js,
        allow_getitem=True)))

#to_js_converters.append((
#    object,
#    lambda original_obj: decorate_to_js(original_obj, False)))

def proxy_to_js(original_obj):
    for t, func in to_js_converters:
        if isinstance(original_obj, t):
            return func(original_obj)
    #print "!"*100, 1, 'NOT CONVERTED:', original_obj
    #print "!"*100, 2, 'NOT CONVERTED:', type(original_obj)
    return decorate_to_js(original_obj)


##################################

cfg_datatypes = {
    'bool': bool,
    'str': str,
    'unicode': unicode,
    'int': int,
    'float': float,
    'list': list,
    'dict': dict}


################################### the javascript module context

class JSContext(PyV8.JSContext):
    def __init__(self, world, *args):
        PyV8.JSContext.__init__(self, world, *args)
        self.world = world
        self.local_objects = dict()

    def assert_context(self):
        assert len(self.local_objects) == 0
        try:
            assert get_js_context(3).entered is False
        except (RuntimeError, AssertionError):
            pass
        else:
            raise RuntimeError('already in javascript context')

    def __enter__(self):
        self.assert_context()
        return PyV8.JSContext.__enter__(self)

    def __exit__(self, *args):
        try:
            PyV8.JSContext.__exit__(self, *args)
        finally:
            self.local_objects.clear()
            self.assert_context()

class JSHoster(PyV8.JSClass):
    __module__ = str(__name__)
    
    def __init__(self):
        PyV8.JSClass.__init__(self)

    # define some modules

    re = secure_object(re, 'search,match,finditer,findall,sub')
    os = secure_object(os, '', 'path')
    os.path = secure_object(os.path, 'splitext')

    # functions needed for this context

    @call_from_js_decorator
    def matcher(self, scheme, host, path, kwargs=None):
        kwargs = kwargs and proxy_from_js(kwargs) or dict()
        return Matcher(scheme, host, path, **kwargs)

    @call_from_js_decorator
    def cfg(self, key, default, type, kwargs=None):
        if kwargs:
            kwargs = proxy_from_js(kwargs)
        else:
            kwargs = dict()
        return cfg(key, default, cfg_datatypes[type], **kwargs)

    # helper functions

    @call_from_js_decorator
    def serialize_html_form(self, form):
        return util.serialize_html_form(proxy_from_js(form))

    @call_from_js_decorator
    def xfilesharing_download(self, resp, step, free):
        return util.xfilesharing_download(proxy_from_js(resp), proxy_from_js(step), proxy_from_js(free))

    @call_from_js_decorator
    def urljoin(self, a, b):
        return urljoin(a, b)

    @call_from_js_decorator
    def dir(self, *args, **kwargs):
        return dir(*args, **kwargs)

    @call_from_js_decorator
    def repr(self, *args, **kwargs):
        return repr(*args, **kwargs)

    @call_from_js_decorator
    def str_format(self, str, args):
        return str.format(*args)

    # javascript console/logging

    class Console(object):
        @call_from_js_decorator
        def log(self, message):
            print 'js.console.log: {}'.format(proxy_from_js(message))

        @call_from_js_decorator
        def alert(self, message):
            print 'js.console.alert: {}'.format(proxy_from_js(message))

        @call_from_js_decorator
        def repr(self, obj):
            print 'js.console.repr: {}'.format(repr(proxy_from_js(obj)))

    console = Console()
    alert = console.alert


# js bootstrap code

js_bootstrap = '''
String.prototype.format = function() {
    args = [];
    for(var i = 0; i < arguments.length; i++) {
        args.push(arguments[i]);
    }
    return str_format(this, args);
}
String.prototype.contains = function(str) {
    return this.indexOf(str) != -1;
}
'''


# module loader functions

def load(name, path, code):
    class Plugin(object):
        __name__ = name
        __file__ = path

    plugin = Plugin()
    
    module = JSHoster()

    with JSContext(module) as jsctx:
        module.plugin = decorate_to_js(plugin, allow_getattr=True, allow_setattr=True)
        jsctx.eval(js_bootstrap)
        jsctx.eval(code)

        # create the this context
        class this:
            pass

        for key in plugin.this.keys():
            value = plugin.this[key]
            if key == 'model':
                value = getattr(hoster_models, value)
            setattr(this, key, value)

        plugin.this = host(this)

    return plugin

def load_file(path):
    with open(path, 'r') as f:
        return load(f.read())
