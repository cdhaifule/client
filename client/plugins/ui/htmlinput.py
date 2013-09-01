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

from pprint import pprint
import gevent
from gevent import pywsgi
from gevent.event import AsyncResult
from bottle import request, Bottle
from ... import logger
from ...localize import _X

log = logger.get("html input")

current_server = None

class WSGIServer(pywsgi.WSGIServer):
    def __init__(self, app):
        pywsgi.WSGIServer.__init__(self, ("127.0.0.1", 0), app)
        self.localport = AsyncResult()
        
    def start_accepting(self):
        self.localport.set(self.socket.getsockname()[1])
        pywsgi.WSGIServer.start_accepting(self)

class Input(object):
    ok_element = None
    cancel_element = None
    
    def __init__(self, input):
        global current_server
        self.input = input
        self.serialized = dict()
        b = Bottle()
        current_server = self.server = WSGIServer(b)
        self.greenserver = gevent.spawn(self.server.serve_forever)
        port = self.server.localport.get()
        self.address = "http://127.0.0.1:{}/".format(port)
        log.info("started input server "+self.address)
        html = "\n".join(self._iter_items())
        pprint(input, width=40, indent=4)
        self.sethtml(html)
        self.end = "CANCEL"
        
        @b.route("/")
        def index():
            return html
        
        @b.route("/post", method="POST")
        def post():
            self.post(request)
            self.close()
            
    def close(self):
        if self.server.started:
            self.server.stop()
        self.hide()
        
    def hide(self, force=False):
        raise NotImplementedError()
        
    def sethtml(self, html):
        raise NotImplementedError()
        
    def compile_text(self, text):
        if isinstance(text, list):
            args, text = text[1], _X(text[0])
            for k, v in args.iteritems():
                text = text.replace(u'#{'+unicode(k)+'}', unicode(v))
        else:
            print "translated", repr(text), repr(_X(text))
            text = _X(text)
        return text

    def post(self, request):
        s = self.serialized
        for i in request.forms:
            if i.endswith(".x") or i.endswith(".y"):
                field, k = i.rsplit(".")
                if field in s:
                    s[field][k == "y"] = int(request.forms[i])
            s[i] = request.forms[i]
        self.end = "OK"
        for item in request.forms.getall("_checkboxes"):
            if not item in s:
                s[item] = False
            else:
                s[item] = True
        
        if self.cancel_element:
            k,v = self.cancel_element
            if s.get(k) == v:
                self.end = "CANCEL"
        
    def _iter_items(self):
        yield """
<html>
<head>
<title>download.am input</title>
<meta charset="utf-8">
<style type="text/css">
body { padding:0; margin:0; }
*{
    font-family:sans-serif;
    outline:none;
    resize:none;
    font-size: 13px;
}
body {
    background:rgb(238, 238, 238);
    color:#484848;
}
button, input[type="submit"], input[type="button"], select {
    padding:0 10px;
    text-align:center;
    cursor:pointer;
    line-height:22px;
    font-family:verdana;
    font-size:11px;
    height: 22px;
    border: 1px solid #bfbfbf;
    border-bottom:1px solid #808080;
    -moz-border-radius: 4px;
    -webkit-border-radius: 4px;
    border-radius: 4px;
    -moz-background-clip: padding;
    -webkit-background-clip: padding-box;
    background-clip: padding-box;
    background-color: #fff;
    -moz-box-shadow: inset 0 0 0 1px rgba(243,243,243,.16);
    -webkit-box-shadow: inset 0 0 0 1px rgba(243,243,243,.16);
    box-shadow: inset 0 0 0 1px rgba(243,243,243,.16);
    background-image: url(data:image/svg+xml;base64,PD94bWwgdmVyc2lvbj0iMS4wIiA/Pgo8c3ZnIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIgd2lkdGg9IjEwMCUiIGhlaWdodD0iMTAwJSIgdmlld0JveD0iMCAwIDEyOCAyNCIgcHJlc2VydmVBc3BlY3RSYXRpbz0ibm9uZSI+PGxpbmVhckdyYWRpZW50IGlkPSJoYXQwIiBncmFkaWVudFVuaXRzPSJvYmplY3RCb3VuZGluZ0JveCIgeDE9IjUwJSIgeTE9Ijk1LjUlIiB4Mj0iNTAlIiB5Mj0iNC40OTk5OTk5OTk5OTk5OSUiPgo8c3RvcCBvZmZzZXQ9IjAlIiBzdG9wLWNvbG9yPSIjZDVkNWQ1IiBzdG9wLW9wYWNpdHk9IjEiLz4KPHN0b3Agb2Zmc2V0PSIxMDAlIiBzdG9wLWNvbG9yPSIjZWZlZmVmIiBzdG9wLW9wYWNpdHk9IjEiLz4KICAgPC9saW5lYXJHcmFkaWVudD4KCjxyZWN0IHg9IjAiIHk9IjAiIHdpZHRoPSIxMjgiIGhlaWdodD0iMjQiIGZpbGw9InVybCgjaGF0MCkiIC8+Cjwvc3ZnPg==);
    background-image: -moz-linear-gradient(bottom, #d5d5d5 4.5%, #efefef 95.5%);
    background-image: -o-linear-gradient(bottom, #d5d5d5 4.5%, #efefef 95.5%);
    background-image: -webkit-linear-gradient(bottom, #d5d5d5 4.5%, #efefef 95.5%);
    background-image: linear-gradient(bottom, #d5d5d5 4.5%, #efefef 95.5%);
}
button:hover, input[type="submit"]:hover, input[type="button"]:hover, select {
    -moz-border-radius: 4px;
    -webkit-border-radius: 4px;
    border-radius: 4px;
    -moz-background-clip: padding;
    -webkit-background-clip: padding-box;
    background-clip: padding-box;
    background-color: #fff;
    -moz-box-shadow: inset 0 0 0 1px rgba(243,243,243,.16);
    -webkit-box-shadow: inset 0 0 0 1px rgba(243,243,243,.16);
    box-shadow: inset 0 0 0 1px rgba(243,243,243,.16);
    background-image: url(data:image/svg+xml;base64,PD94bWwgdmVyc2lvbj0iMS4wIiA/Pgo8c3ZnIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIgd2lkdGg9IjEwMCUiIGhlaWdodD0iMTAwJSIgdmlld0JveD0iMCAwIDEyOCAyNCIgcHJlc2VydmVBc3BlY3RSYXRpbz0ibm9uZSI+PGxpbmVhckdyYWRpZW50IGlkPSJoYXQwIiBncmFkaWVudFVuaXRzPSJvYmplY3RCb3VuZGluZ0JveCIgeDE9IjUwJSIgeTE9Ijk1LjUlIiB4Mj0iNTAlIiB5Mj0iNC40OTk5OTk5OTk5OTk5OSUiPgo8c3RvcCBvZmZzZXQ9IjAlIiBzdG9wLWNvbG9yPSIjZTdlN2U3IiBzdG9wLW9wYWNpdHk9IjEiLz4KPHN0b3Agb2Zmc2V0PSIxMDAlIiBzdG9wLWNvbG9yPSIjZjdmN2Y3IiBzdG9wLW9wYWNpdHk9IjEiLz4KICAgPC9saW5lYXJHcmFkaWVudD4KCjxyZWN0IHg9IjAiIHk9IjAiIHdpZHRoPSIxMjgiIGhlaWdodD0iMjQiIGZpbGw9InVybCgjaGF0MCkiIC8+Cjwvc3ZnPg==);
    background-image: -moz-linear-gradient(bottom, #e7e7e7 4.5%, #f7f7f7 95.5%);
    background-image: -o-linear-gradient(bottom, #e7e7e7 4.5%, #f7f7f7 95.5%);
    background-image: -webkit-linear-gradient(bottom, #e7e7e7 4.5%, #f7f7f7 95.5%);
    background-image: linear-gradient(bottom, #e7e7e7 4.5%, #f7f7f7 95.5%);
}
button:active, input[type="submit"]:active, input[type="button"]:active {
    border: 1px solid #3f8faf;
    border-bottom:1px solid #60b0d0;
    -moz-border-radius: 4px;
    -webkit-border-radius: 4px;
    border-radius: 4px;
    -moz-background-clip: padding;
    -webkit-background-clip: padding-box;
    background-clip: padding-box;
    background-color: #fff;
    -moz-box-shadow: inset 0 0 0 1px #7bcbeb, inset 0 0 0 1px rgba(243,243,243,.16);
    -webkit-box-shadow: inset 0 0 0 1px #7bcbeb, inset 0 0 0 1px rgba(243,243,243,.16);
    box-shadow: inset 0 0 0 1px #7bcbeb, inset 0 0 0 1px rgba(243,243,243,.16);
    background-image: url(data:image/svg+xml;base64,PD94bWwgdmVyc2lvbj0iMS4wIiA/Pgo8c3ZnIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIgd2lkdGg9IjEwMCUiIGhlaWdodD0iMTAwJSIgdmlld0JveD0iMCAwIDEyOCAyNCIgcHJlc2VydmVBc3BlY3RSYXRpbz0ibm9uZSI+PGxpbmVhckdyYWRpZW50IGlkPSJoYXQwIiBncmFkaWVudFVuaXRzPSJvYmplY3RCb3VuZGluZ0JveCIgeDE9IjUwJSIgeTE9Ijk1LjUlIiB4Mj0iNTAlIiB5Mj0iNC40OTk5OTk5OTk5OTk5OSUiPgo8c3RvcCBvZmZzZXQ9IjAlIiBzdG9wLWNvbG9yPSIjZDVkNWQ1IiBzdG9wLW9wYWNpdHk9IjEiLz4KPHN0b3Agb2Zmc2V0PSIxMDAlIiBzdG9wLWNvbG9yPSIjZWZlZmVmIiBzdG9wLW9wYWNpdHk9IjEiLz4KICAgPC9saW5lYXJHcmFkaWVudD4KCjxyZWN0IHg9IjAiIHk9IjAiIHdpZHRoPSIxMjgiIGhlaWdodD0iMjQiIGZpbGw9InVybCgjaGF0MCkiIC8+Cjwvc3ZnPg==);
    background-image: -moz-linear-gradient(bottom, #d5d5d5 4.5%, #efefef 95.5%);
    background-image: -o-linear-gradient(bottom, #d5d5d5 4.5%, #efefef 95.5%);
    background-image: -webkit-linear-gradient(bottom, #d5d5d5 4.5%, #efefef 95.5%);
    background-image: linear-gradient(bottom, #d5d5d5 4.5%, #efefef 95.5%);
}
input[type="text"], input[type="password"] {
    height: 21px;
    border: 1px solid #d2d2d2; /* stroke */
    -moz-border-radius: 6px;
    -webkit-border-radius: 6px;
    border-radius: 6px; /* border radius */
    -moz-background-clip: padding;
    -webkit-background-clip: padding-box;
    background-clip: padding-box; /* prevents bg color from leaking outside the border */
    background-color: #fff; /* layer fill content */
    -moz-box-shadow: inset 0 1px 5px rgba(0,0,0,.15); /* inner shadow */
    -webkit-box-shadow: inset 0 1px 5px rgba(0,0,0,.15); /* inner shadow */
    box-shadow: inset 0 1px 5px rgba(0,0,0,.15); /* inner shadow */
    background-image: url(data:image/svg+xml;base64,PD94bWwgdmVyc2lvbj0iMS4wIiA/Pgo8c3ZnIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIgd2lkdGg9IjEwMCUiIGhlaWdodD0iMTAwJSIgdmlld0JveD0iMCAwIDI5NyAyOSIgcHJlc2VydmVBc3BlY3RSYXRpbz0ibm9uZSI+PGxpbmVhckdyYWRpZW50IGlkPSJoYXQwIiBncmFkaWVudFVuaXRzPSJvYmplY3RCb3VuZGluZ0JveCIgeDE9IjUwJSIgeTE9IjEwMCUiIHgyPSI1MCUiIHkyPSItMS40MjEwODU0NzE1MjAyZS0xNCUiPgo8c3RvcCBvZmZzZXQ9IjAlIiBzdG9wLWNvbG9yPSIjZmZmIiBzdG9wLW9wYWNpdHk9IjEiLz4KPHN0b3Agb2Zmc2V0PSIxMDAlIiBzdG9wLWNvbG9yPSIjZjdmN2Y3IiBzdG9wLW9wYWNpdHk9IjEiLz4KICAgPC9saW5lYXJHcmFkaWVudD4KCjxyZWN0IHg9IjAiIHk9IjAiIHdpZHRoPSIyOTciIGhlaWdodD0iMjkiIGZpbGw9InVybCgjaGF0MCkiIC8+Cjwvc3ZnPg==); /* gradient overlay */
    background-image: -moz-linear-gradient(bottom, #fff 0%, #f7f7f7 100%); /* gradient overlay */
    background-image: -o-linear-gradient(bottom, #fff 0%, #f7f7f7 100%); /* gradient overlay */
    background-image: -webkit-linear-gradient(bottom, #fff 0%, #f7f7f7 100%); /* gradient overlay */
    background-image: linear-gradient(bottom, #fff 0%, #f7f7f7 100%); /* gradient overlay */
}
input[type="text"]:active, input[type="text"]:focus,input[type="password"]:active, input[type="password"]:focus {
    border: 1px solid #3f8faf;
    border-bottom: 1px solid #60b0d0;
}

a {
    text-decoration:none;
    color:blue;
    cursor: hand;
}

td {
    vertical-align: middle;
}

</style>
<script type="text/javascript">
    window.onload = function() {
        x = document.getElementById('foo')
        window.resizeTo(x.offsetWidth+5, x.offsetHeight+15);
        var elements = document.getElementsByTagName('input');
        for(var i in elements) {
            if(elements[i].type=='text') {
                elements[i].focus();
                break;
            }
        }
    }
    function closing_window() {
        var t = document.getElementById("cancel_element");
        if (t){
            t.click();
            return "done";
        }
        return "";
    }
</script>
</head>
<body oncontextmenu="return false;">
<div id="foo" style="display:inline-block;margin-left: 3px; min-width: 80px;">
<form method="post" name="inputform" action="/post">
"""
        self.text_align = "left"
        for i in self.build_elements(self.input.elements):
            if isinstance(i, unicode):
                i = i.encode("utf-8")
            yield i
        yield """</form></div></body></html>"""

    def build_elements(self, elements):
        maxcols = max(len(row) for row in elements) or 1
        yield "<table>"
        for row in elements:
            rows = self.build_rows(row, maxcols)
            try:
                first = rows.next()
            except StopIteration:
                continue
            else:
                yield '<tr>'
                yield first
                for i in rows:
                    yield i
                yield "</tr>"
        yield "</table>"
    
    def build_rows(self, row, maxcols=1):
        for col, e in enumerate(row):
            if e is None:
                continue
            colspan = ""
            add = ""
            if len(row) == col + 1:
                _cs = maxcols - col
                if _cs > 1:
                    colspan = " colspan={}".format(_cs)
            if 'type' in e:
                func = 'input_{}_{}'.format(e["element"], e["type"])
            else:
                func = 'input_{}'.format(e.element)
            if col > 0:
                add = "padding-left: 5px;"
            if not hasattr(self, func):
                raise RuntimeError('element "{}" not implemented'.format(func))
            else:
                r = getattr(self, func)(e)
                if r is None:
                    continue
                yield '    <td style="text-align: {};{}"{}>'.format(self.text_align, add, colspan)
                if not isinstance(r, basestring):
                    for i in r:
                        yield " "*8 + i
                else:
                    yield " "*8 + r
                yield '    </td>'
    
    def input_text(self, e):
        content = self.compile_text(e["content"]).strip()
        if not content:
            return "&nbsp;"
        else:
            return content
        
    def input_image(self, e, f="none"):
        return u"""<img src="data:{};base64,{}" />""".format(e.mime, e.data)

    def input_image_submit(self, e):
        self.serialized[e.name] = [0, 0]
        return u"""<input type="image" name="{}" src="data:{};base64,{}" style="cursor:crosshair;" />""".format(e.name, e.mime, e.data)

    def input_input_text(self, e, _type="text"):
        return u"""<input type="{}" name="{}" value="{}" />""".format(_type, e.name, self.compile_text(e.value))
        
    def input_input_password(self, e):
        return self.input_input_text(e, "password")
        
    def input_input_checkbox(self, e):
        default = "checked" if e.default else ""
        return u"""<input type="hidden" name="_checkboxes" value="{0}" /><input type="checkbox" name="{0}" {1} /> {2}""".format(e.name, default, self.compile_text(e.label))

    def input_select_dropdown(self, e):
        yield u"""<select name="{}">""".format(e.name)
        for i in e.options:
            if isinstance(i, tuple):
                yield u"""    <option value="{}">{}</option>""".format(i[0], self.compile_text(i[1]))
            else:
                yield u"""    <option>{}</option>""".format(i)
        yield "</select>"

    def input_button_submit(self, e, sc=False):
        sc = e.get("sc")
        e["content"] = self.compile_text(e.get("content") or "OK")
        e["name"] = e.get("name", e["content"])
        setid = " "
        if e.get("ok") or not sc:
            if self.ok_element:
                raise RuntimeError("OK element already set")
            self.ok_element = e["name"], e["value"]
            setid = ' id="ok_element" '
        elif e.get("cancel"):
            if self.cancel_element:
                raise RuntimeError("Cancel element already set")
            self.cancel_element = e["name"], e["value"]
            setid = ' id="cancel_element" '
        e["setid"] = setid
        
        return u"""<button{setid}name="{name}" value="{value}" type="submit">{content}</button>""".format(**e)

    def input_button_cancel(self, e):
        e["content"] = self.compile_text(e.get("content") or "Cancel")
        e["cancel"] = True
        e["value"] = e.get("value") or e["content"]
        
        return self.input_button_submit(e, True)
        
    def input_button_choice(self, e):
        for f in e.choices:
            f["element"] = "button"
            f["type"] = "submit"
            f["sc"] = True
            f["name"] = e["name"]
        return self.build_elements([e.choices])
        
    def input_link(self, e):
        return u'<a onclick="alert(\'tab {}\'); return false;">{}</a>'.format(e.url, self.compile_text(e.content))

    def input_subbox(self, e):
        return self.build_elements(e.elements)

    def input_float(self, e):
        self.text_align = e.direction
