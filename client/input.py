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

import time
import base64

import gevent
from gevent.event import AsyncResult

from . import event, interface, scheme

from .config import globalconfig

config = globalconfig.new('input')
config.default("localui", True, bool, description="Use desktop ui input")

class InputError(BaseException):
    pass

class InputAborted(InputError):
    pass

class InputTimeout(InputError):
    pass

class InputFailed(InputError):
    pass

class BaseInput(dict):
    def __getattr__(self, key):
        return self[key]

    def __setattr__(self, key, value):
        self[key] = value

class Subbox(BaseInput):
    def __init__(self, *elements):
        self['element'] = 'subbox'
        self['elements'] = list(elements)

class Image(BaseInput):
    def __init__(self, data, mime, name=None):
        """type: show: just show image, submit: on click submit coordinates of the click"""
        self['element'] = 'image'
        if callable(data):
            data = data()
            try:
                mime, data = data
            except ValueError:
                pass
        self['data'] = base64.standard_b64encode(data)
        self['mime'] = mime
        self['name'] = name
        
class ImageSubmit(Image):
    def __init__(self, data, mime, name=None):
        """type: show: just show image, submit: on click submit coordinates of the click"""
        self['element'] = 'image_submit'
        self['data'] = base64.standard_b64encode(data)
        self['mime'] = mime
        self['name'] = name

class Text(BaseInput):
    def __init__(self, content):
        self['element'] = 'text'
        self['content'] = content

class Link(BaseInput):
    def __init__(self, url, content):
        self['element'] = 'link'
        self['url'] = url
        self['content'] = content

class Button(BaseInput):
    type = 'button'

    def __init__(self, name=None, value=None, content=None, ok=False, cancel=False):
        self['element'] = 'button'
        self['type'] = self.type
        self['name'] = name
        self['value'] = value
        if not content:
            content = value
        self['content'] = content

class Submit(Button):
    type = 'submit'

class Choice(Button):
    type = 'choice'

    def __init__(self, name=None, choices=None):
        self['element'] = 'button'
        self['type'] = self.type
        self['name'] = name
        self['choices'] = [self.choice(i) for i in (choices or list())]
        
    def choice(self, value, content=None, ok=False, cancel=False, link=None):
        if isinstance(value, dict):
            try:
                content = value.get("content", value.get("value"))
                ok = value.get('ok', False)
                cancel = value.get('cancel', False)
                link = value.get("link", None)
                value = value["value"]
            except KeyError:
                raise KeyError("Choice requires a value")
        elif isinstance(value, tuple):
            value, content = value
            ok, cancel = False, False

        if ok and cancel:
            raise RuntimeError('ok and cancel cannot be true')

        if not content:
            content = value
        d = dict(value=value, content=content, ok=ok, cancel=cancel, link=link)
        try:
            self.choices.append(d)
        except KeyError:
            return d
        
class Input(BaseInput):
    def __init__(self, name, type="text", value=None, default="", label=None):
        """type: text, password, radio, checkbox, hidden"""
        self['element'] = 'input'
        self['name'] = name
        self['type'] = type
        self['value'] = value
        self['default'] = default
        self['label'] = label

class OpenFile(BaseInput):
    def __init__(self, name, value=None, caption='Browse', filetypes=None, initialdir=None, initialfile=None):
        """type: text, password, radio, checkbox, hidden"""
        self['element'] = 'openfile'
        self['name'] = name
        self['value'] = value
        self['caption'] = caption
        self['filetypes'] = filetypes
        self['initialdir'] = initialdir
        self['initialfile'] = initialfile

class Radio(BaseInput):
    def __init__(self, name, default=None, value=None):
        self['element'] = 'radio'
        self['name'] = name
        self['default'] = default
        self['value'] = value

class Select(BaseInput):
    def __init__(self, name, options, type="dropdown", default=None):
        """type options: dropdown: show only one, list: expanded list,
        onchange: submit
        options values can be arrays [[name, value], [name, value], ...] or just values [value, value, ...]"""
        self['element'] = 'select'
        self['name'] = name
        self['options'] = options
        self['type'] = type
        self['default'] = default

class Float(BaseInput):
    def __init__(self, direction):
        self['element'] = 'float'
        self['direction'] = direction

input_tables = {}

class InputTable(scheme.Table):
    _table_name = 'input'
    _table_collection = input_tables

    id = scheme.Column('api')
    type = scheme.Column('api')
    parent = scheme.Column('api', lambda self, value: value and [value._table_name, value.id] or None)
    timeout = scheme.Column('api', lambda self, timeout: timeout and int(timeout.eta*1000) or None)
    elements = scheme.Column('api')
    result = scheme.Column('api')
    close_aborts = scheme.Column('api')

    ignore_api = False

    def __init__(self, type, parent, timeout, elements, close_aborts, ignore_api=False):
        self.type = type
        self.parent = parent
        self.timeout = None
        self.elements = [isinstance(e, list) and e or [e] for e in elements]
        self.close_aborts = close_aborts
        self.ignore_api = ignore_api

        if parent:
            parent.input = self
        
        self._result = AsyncResult()
        self.reset_timeout(timeout)

    def set_result(self, value):
        if self._result.ready():
            #raise RuntimeError('result of input already set')
            return
        with scheme.transaction:
            self.result = value
            self.reset_timeout(None)
        self._result.set(value)
        event.fire("input:result", self)

    def set_error(self, value):
        if self._result.ready():
            #raise RuntimeError('result of input already set')
            return
        with scheme.transaction:
            self.result = str(value)
            self.reset_timeout(None)
        self._result.set_exception(value)
        event.fire("input:error", self)

    def reset_timeout(self, timeout):
        with scheme.transaction:
            if self.timeout:
                self.timeout.kill()
            if timeout:
                self.timeout = gevent.spawn_later(timeout, self._timed_out)
                self.timeout.eta = time.time() + timeout
            elif self.timeout:
                self.timeout = None

    def _timed_out(self):
        with scheme.transaction:
            self.timeout = None
            self.set_error(InputTimeout())

def get(elements, timeout=60, type=None, parent=None, close_aborts=False, ignore_api=False):
    """note: function is NOT using _interpret_result
    """
    with scheme.transaction:
        input = InputTable(type, parent, timeout, elements, close_aborts, ignore_api)

    if config.localui:
        event.fire("input:uirequest", input)
    event.fire("input:request", input)

    try:
        return input._result.get()
    finally:
        with scheme.transaction:
            input.reset_timeout(None)
            if input.parent:
                input.parent.input = None
            input.table_delete()
        event.fire("input:done", input)

def captcha_text(data, mime, message="Please input the following Captcha:", timeout=60, parent=None, close_aborts=False, browser=None):
    elements = []
    if message:
        elements += [Text(message)]
    elements += [Image(data, mime), Input('captcha'), Submit()]
    result = get(elements, timeout, 'captcha_text', parent=parent, close_aborts=close_aborts)
    return result and result['captcha'] or result
    
captcha = captcha_text

def captcha_image(data, mime, message="Please click on the right place:", timeout=60, parent=None, close_aborts=False, browser=None):
    elements = []
    if message:
        elements += [Text(message)]
    elements += [ImageSubmit(data, mime, 'captcha')]
    result = get(elements, timeout, 'captcha_image', parent=parent, close_aborts=close_aborts)
    return result and result['captcha'] or result

def password(message="Enter password:", timeout=60, parent=None, close_aborts=False):
    elements = []
    if message:
        elements += [Text(message)]
    elements += [Input("password", "password"), Submit()]
    result = get(elements, timeout, 'password', parent=parent, close_aborts=close_aborts)
    return result and result['password'] or result

def password_www(message="Enter password:", timeout=60, parent=None, close_aborts=False):
    return password(message=message, timeout=timeout, parent=parent, close_aborts=close_aborts)
    
def reset_timeout(id, timeout):
    if not timeout is None:
        timeout = time.time() + timeout - 0.2
    event.fire("input:reset_timeout", id, timeout)

def input_loop(testfunc=None, prefunc=None, retries=1, func=get, **kwargs):
    """retry input with callback.
    arguments:
        testfunc - callback function to test result.
                   this function must return not None when successful
                   when testfunc is None the function returns when any result is present
        prefunc - callback function called before func(**kwargs)
        retries - number of retries
        func - input function (defaults to "get")
    """
    for i in xrange(retries):
        if prefunc:
            prefunc()
        result = func(**kwargs)
        if result:
            if testfunc is None:
                return result
            t = testfunc(result)
            if not t is None:
                return t
    raise InputFailed()
    
def input_iter(retries=1, func=get, prefunc=None, **kwargs):
    for i in xrange(retries):
        if prefunc:
            prefunc(kwargs)
        yield func(**kwargs)
    raise InputFailed()

@interface.register
class AnswerMachine(interface.Interface):
    name = "input"
    
    def answer(id=None, answer=None):
        """Answer to resid's request, args: resid, answer"""
        try:
            input_tables[id].set_result(answer)
        except KeyError:
            pass
    
    def abort(id=None):
        try:
            input_tables[id].set_error(InputAborted())
        except KeyError:
            pass
    
    def reset_timeout(id=None, timeout=None):
        """reset timeout of a resid to timeout or remove timeout"""
        try:
            input_tables[id].reset_timeout(timeout)
        except KeyError:
            pass

    def request(id=None):
        try:
            return input_tables[id].serialize(set(['api']))
        except KeyError:
            pass
