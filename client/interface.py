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

import types
import gevent

from . import event, logger

ignore_protected_functions = False

pyotp = False

log = logger.get('interface')

class InterfaceManager(dict):
    def add(self, interface):
        if interface.name in self:
            raise ValueError('interface "{}" already exists'.format(interface.name))
        self[interface.name] = interface
        event.fire('interface:new', interface)
        return interface
        
    def call(self, _name, _funcname, _responder=None, **kwargs):
        try:
            method = getattr(self[_name], _funcname)
        except KeyError:
            log.error('unknown interface: {}, requested method {}'.format(_name, _funcname))
            return
        except AttributeError:
            log.error('unknown interface method: {}.{}'.format(_name, _funcname))
            return
        args = []
        if method.__self__ is not None:     # use implicit static methods or self if instanced.
            args.append(method.__self__)    # or for classmethods
        func = method.__func__
        if 'responder' in func.func_code.co_varnames:
            kwargs['responder'] = _responder
        #this would not work with functions with *args or **kwargs
        #argnames = func.func_code.co_varnames
        #kwargs = {k:v for k, v in kwargs.iteritems() if k in argnames}
        try:
            #print "EXEC", _name+'.'+_funcname, args, kwargs
            result = func(*args, **kwargs)
            if isinstance(result, types.GeneratorType):
                result = list(result)
            elif result is not None and not isinstance(result, (set, tuple, list, dict)):
                result = dict(result=result)
            return result
        except gevent.GreenletExit:
            pass

    def remove(self, name):
        if name not in self:
            raise ValueError('interface "{}" not exists'.format(name))
        interface = self[name]
        del self[name]
        event.fire('interface:removed', interface)

class Interface(object):
    name = None

    @classmethod
    def list_functions(cls):
        """lists all functions that this interface has"""
        functions = []
        for name in dir(cls):
            func = getattr(cls, name)
            if callable(func) and not name.startswith('_'):
                functions.append(dict(name=name, doc=func.__doc__ and func.__doc__.strip() or func.__doc__))
        return functions
        
def guest_protected_dialog():
    from .input import Text, Choice, get, InputTimeout, InputError
    elements = [Text('The website wants to change or do something that could harm or damage your computer.')]
    elements += [Text('If you ordered it to do so, no need to worry.')]
    elements += [Choice('answer', choices=[
        {"value": "ok", "content": "I understand"},
        {"value": "cancel", "content": "No"}
    ])]
    try:
        r = get(elements, type='guest_protected_api', timeout=120)
        result = r['answer']
    except InputTimeout:
        result = 'cancel'
    except InputError:
        result = 'cancel'
    except KeyError: # don't know why this can happen
        result = 'cancel'
    return result == "ok"

def protected(func):
    if ignore_protected_functions:
        return func

    def fn(protected_key=None, *args, **kwargs):
        from . import login
        if protected_key == "guest" and login.is_guest():
            if guest_protected_dialog():
                return func(*args, **kwargs)
        if pyotp and protected_key.isdigit():
            raise NotImplementedError()
        if protected_key != login.get('protected'):
            raise ValueError('Invalid protected key')
        return func(*args, **kwargs)
    return fn

manager = InterfaceManager()
register = manager.add      # use as class decorator
call = manager.call

@register
class InterfaceInterface(Interface):
    name = 'interface'

    def list_modules():
        """lists all available interface modules"""
        modules = []
        for name, module in manager.iteritems():
            modules.append(dict(name=name, doc=module.__doc__ and module.__doc__.strip() or module.__doc__))
        return modules

def init():
    pass
