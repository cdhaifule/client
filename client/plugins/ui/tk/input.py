# -*- coding: utf-8 -*-
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
import gevent
import base64
import webbrowser

from Tkinter import Tk, Frame, Label, Checkbutton, OptionMenu, Button, StringVar, IntVar, Entry, FALSE, LEFT
from PIL import ImageTk

from gevent.lock import Semaphore

from .animate import AnimatedImgLabel
from .... import interface, event, ui, settings
from ....localize import _X

class _FakeText:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value

    def get(self):
        return self.value

class Context(object):
    def __init__(self, master, parent, elements):
        self.master = master
        self.parent = parent
        self.elements = elements

        self.frame = Frame(parent)
        self.row = 0
        self.col = 0
        self.columnspan = 1
        self.sticky = None

        self.columns = max(len(row) for row in elements) or 1

        for row in elements:
            for col in xrange(len(row)):
                e = row[col]
                if e is None:
                    continue
                if len(row) == col + 1:
                    self.columnspan = self.columns - col
                else:
                    self.columnspan = 1
                self.col = col
                if 'type' in e:
                    func = 'input_{}_{}'.format(e.element, e.type)
                else:
                    func = 'input_{}'.format(e.element)
                if not hasattr(self, func):
                    raise RuntimeError('element "{}" not implemented'.format(func))
                else:
                    getattr(self, func)(e)
            self.row += 1

    def pack_element(self, w):
        w.grid(row=self.row, column=self.col, columnspan=self.columnspan, sticky=self.sticky)

    def compile_text(self, text):
        if isinstance(text, list):
            args, text = text[1], _X(text[0])
            for k, v in args.iteritems():
                text = text.replace('#{'+str(k)+'}', str(v))
        else:
            text = _X(text)
        if self.master.input.parent:
            for key in self.master.input.parent.__dict__.iterkeys():
                try:
                    text = text.replace('#{%s}' % key, str(getattr(self.master.input.parent, key)))
                except:
                    pass
        return text

    def input_float(self, e):
        if e.direction == 'left':
            self.sticky = "W"
        elif e.direction == 'right':
            self.sticky = "E"
        elif e.direction == 'center':
            self.sticky = None
        else:
            raise ValueError('unknown float direction'.format(e.direction))

    def input_text(self, e):
        self.pack_element(Label(self.frame, text=self.compile_text(e.content)))

    def input_link(self, e):
        w = Label(self.frame, text=self.compile_text(e.content), foreground='#0000ff')
        w.bind('<1>', lambda event: webbrowser.open_new_tab(e.url))
        self.pack_element(w)

    def input_image(self, e):
        if 0 and e.mime in {"image/gif", "image/webp"}:
            w = AnimatedImgLabel(self.frame, e.data)
        else:
            img = ImageTk.PhotoImage(data=base64.b64decode(e.data), master=self.frame)
            w = Label(self.frame, image=img)
            w.image = img
        self.pack_element(w)
        return w

    def input_image_submit(self, e):
        def on_mouse(event):
            self.master.serialized[e.name] = (event.x, event.y)
            self.on_ok()
        w = self.input_image(e)
        w.bind('<Button-1>', on_mouse)

    def input_input_text(self, e, **kwargs):
        var = StringVar(master=self.frame)
        self.master.serialized[e.name] = lambda: var.get()
        if e.value is not None:
            var.set(e.value)
        w = Entry(self.frame, textvariable=var, **kwargs)
        self.pack_element(w)
        w.focus_set()
        return w

    def input_input_password(self, e):
        self.input_input_text(e, show='*')

    def input_input_checkbox(self, e):
        var = IntVar(master=self.frame)
        self.master.serialized[e.name] = lambda: var.get() and True or False
        w = Checkbutton(self.frame, text=self.compile_text(e.label), variable=var)
        self.pack_element(w)

    def input_select_dropdown(self, e):
        var = StringVar(master=self.frame)
        self.master.serialized[e.name] = lambda: var.get()
        if e.default is not None:
            var.set(e.default)
        w = OptionMenu(self.frame, var, *e.options)
        self.pack_element(w)

    def input_button_submit(self, e):
        w = Button(self.frame, text=self.compile_text(e.content or 'OK'), command=lambda: self.submit(e, self.on_ok), padx=5)
        self.pack_element(w)
        if self.master.ok_element:
            raise RuntimeError('ok element already set')
        self.master.ok_element = e

    def input_button_cancel(self, e):
        w = Button(self.frame, text=self.compile_text(e.content or 'Cancel'), command=lambda: self.submit(e, self.on_cancel), padx=5)
        self.pack_element(w)
        if self.master.cancel_element:
            raise RuntimeError('cancel element already set')
        self.master.cancel_element = e

    def input_button_choice(self, e):
        frame = Frame(self.frame)
        for f in e.choices:
            if f['ok']:
                if self.master.ok_element:
                    raise RuntimeError('ok element already set')
                self.master.ok_element = dict(name=e.name, value=f['value'])
            elif f['cancel']:
                if self.master.cancel_element:
                    raise RuntimeError('cancel element already set')
                self.master.cancel_element = dict(name=e.name, value=f['value'])

            def create_button(f):
                def command():
                    if f['link']:
                        webbrowser.open_new_tab(f['link'])
                    self.submit(data, self.on_ok)

                data = dict(name=e.name, value=f['value'])
                Button(frame, text=self.compile_text(f['content']),
                    command=command,
                    padx=5).pack(side=LEFT, padx=5, pady=5)

            create_button(f)
        self.pack_element(frame)

    def input_subbox(self, e):
        ctx = Context(self.master, self.frame, e.elements)
        self.pack_element(ctx.frame)

    def submit(self, e, func):
        self.master.submit(e, func)

    def on_ok(self):
        self.master.on_ok()

    def on_cancel(self):
        self.master.on_cancel()


class _Input(Tk):
    def __init__(self, input):
        Tk.__init__(self)
        self.input = input

        if sys.platform == "win32":
            self.iconbitmap(bitmap=settings.mainicon)
        else:
            img = ImageTk.PhotoImage(master=self, file=settings.mainicon)
            self.tk.call('wm', 'iconphoto', self._w, img)
        self.resizable(width=FALSE, height=FALSE)

        self.end = None
        self.serialized = dict()

        self.title('Download.am')

        self.ok_element = None
        self.cancel_element = None

        self.bind('<Return>', lambda event: self.unbound_ok())
        self.bind('<Escape>', lambda event: self.unbound_cancel())
        if input.close_aborts:
            self.protocol("WM_DELETE_WINDOW", self.unbound_cancel)
        else:
            self.protocol("WM_DELETE_WINDOW", self.destroy)

        ctx = Context(self, self, input.elements)
        ctx.frame.pack()

        self.focus_force()

    def mainloop(self):
        while self.end is None:
            gevent.sleep(0.01)
            try:
                self.update()
            except:
                break

    def unbound_ok(self):
        if self.ok_element is not None:
            self.submit(self.ok_element, self.on_ok)
        else:
            self.on_ok()

    def unbound_cancel(self):
        if self.cancel_element is not None:
            self.submit(self.cancel_element, self.on_cancel)
        else:
            self.on_cancel()

    def submit(self, e, func):
        self.serialized[e['name']] = e['value']
        if func:
            func()

    def on_ok(self):
        self.end = 'OK'
        self.destroy()

    def on_cancel(self):
        self.end = 'CANCEL'
        self.destroy()

    def finalize(self):
        for key, value in self.serialized.iteritems():
            if callable(value):
                self.serialized[key] = value()

lock = Semaphore()
windows = dict()

@event.register('input:request')
def input(e, input):
    if input.ignore_api or not ui.browser_has_focus():
        gevent.spawn(_input, input)

def _input(input):
    with lock:
        win = _Input(input)
        windows[input.id] = win
        try:
            ui.hide_splash()
            win.mainloop()
            if win.end == 'OK':
                win.finalize()
                interface.call('input', 'answer', id=input.id, answer=win.serialized)
            elif win.end == 'CANCEL':
                interface.call('input', 'abort', id=input.id)
        finally:
            ui.show_splash()
            del windows[input.id]

@event.register("input:done")
def _(e, input):
    if input.id in windows:
        try:
            windows[input.id].destroy()
            windows[input.id].end = 'INPUT:DONE'
        except:
            pass
