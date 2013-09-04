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

import uuid
import base64
import gevent
import socket
import platform

from bottle import Bottle, request, response, HTTPError, SimpleTemplate
from socketio import socketio_manage, server
from socketio.namespace import BaseNamespace
from socketio.mixins import BroadcastMixin

from . import proto, client
from .. import settings, logger, localrpc, login, localize

log = logger.get("api.server")

app = Bottle()

class Namespace(BaseNamespace, BroadcastMixin):
    def __init__(self, *args, **kwargs):
        BaseNamespace.__init__(self, *args, **kwargs)
        BroadcastMixin.__init__(self)
        self.disconnected = False

    #def initalize(self):
        #proto.add_connection(self)
        
    def recv_error(self, packet):
        print "error recv", packet
        pass
    
    def on_message(self, message):
        message = proto.unpack_message(message)
        gevent.spawn(proto.process_message, self.send_message, message)

    def send_message(self, message):
        #print ">>> SEND DIRECT", message['flags'], message.get('command'), message['payload']
        self.emit('message', message)

    def disconnect(self, *args, **kwargs):
        if self.disconnected is False:
            self.disconnected = True
            self.emit('client_leave', settings.app_uuid)
        try:
            proto.remove_connection(self)
        except ValueError:
            pass
        print "client disconnecting", args, kwargs
        
    def on_hello(self, uuid, origin):
        print "received on_hello", uuid, origin
        if str(uuid) == str(settings.app_uuid):
            self.emit('client_join', settings.app_uuid)
            proto.add_connection(self)
        
    def on_bye(self, uuid):
        print "received on_bye", uuid
        if str(uuid) == str(settings.app_uuid):
            proto.remove_connection(self)
        
    def on_service(self, *args):
        print "received on_service", args
        return False

@app.route('/')
def route_index():
    return "websocket client listening"

@app.route('/downloadam.js')
def route_downloadam_js():
    if not login.has_login():
        return
    response.headers['Content-Type'] = 'text/javascript'
    _uuid = base64.b64encode(login.encrypt('frontend', settings.app_uuid))
    return 'var downloadam_client_id="{}";'.format(_uuid)
    
@app.route('/socket.io/<arg:path>')
def route_socket_io(*arg, **kw):
    request.environ["HTTP_ORIGIN"] = "*.download.am"
    socketio_manage(request.environ, {'': Namespace}, request=request)
    return "out"

allowed_origins = [
    'http://development-downloadam.s3-external-3.amazonaws.com/',
    'http://stable-downloadam.s3-external-3.amazonaws.com/',
    'http://download.am/',
    'http://www.download.am/',
    'http://localhost:9090/change_login',
    'http://local.download.am:9090/change_login'
]

def check_referer(referer):
    for o in allowed_origins:
        if referer.startswith(o):
            return False
    return True

@app.route('/change_login')
def route_login_dialog():
    _id = "/" + uuid.uuid4().hex
    username = request.query.username
    if login.config.username == username and login.has_login() and client.client.is_connected():
        return login_template.render(_=localize._X, action='logged_in', machine_name=socket.gethostname(), os_name=platform.system(), username=username)

    @app.route(_id, method='POST')
    def change_login():
        if check_referer(request.environ['HTTP_REFERER']):
            response.status = 403
            return login_template.render(_=localize._X, action='denied')
        try:
            app.routes.remove(route)
        except ValueError:
            return HTTPError(404)
        login.set_login(username, request.params.password)
        return login_template.render(_=localize._X, action='close', machine_name=socket.gethostname(), os_name=platform.system(), username=username)
    route = app.routes[-1]
    return login_template.render(_=localize._X, action='ok', login_url=_id, machine_name=socket.gethostname(), os_name=platform.system(), username=username)

handle = None

def init():
    global handle
    try:
        handle = server.SocketIOServer(('127.0.0.1', 9090), app, policy_server=False, heartbeat_interval=4, heartbeat_timeout=20, close_timeout=300)
        if not handle.started:
            handle.start()
        localrpc.prevent_socket_inheritance(handle.socket)
    except:
        log.unhandled_exception('error starting local socketio server')

def terminate():
    global handle

    for conn in proto.connections[:]:
        if isinstance(conn, Namespace):
            conn.emit('client_leave', settings.app_uuid)
            proto.remove_connection(conn)

    if handle:
        handle.stop()
        handle = None
        
login_template = SimpleTemplate("""
<html>
    <head>
        <style type="text/css">
            body {
                margin: 0;
                padding: 0;
                background: url(https://www.download.am/assets/img/ui/background_top.png) repeat-x top #fcfcfc;
                font-size: 14px;
                font-weight: normal;
            }
            p {
                display: block;
                width: 100%;
                text-align: center;
                color: #484848;
                margin-top: 75px;
                text-shadow: 1px whitesmoke;
            }
            .buttons {
                margin-top:20px;
                text-align: center;
            }
            .blue {
                cursor: pointer;
                position: relative;
                text-align: center;
                color: #ffffff;
                padding: 10px;
                width:100px;
                border: 1px solid #2373af;
                -moz-border-radius: 5px;
                -webkit-border-radius: 5px;
                border-radius: 5px;
                -moz-background-clip: padding;
                -webkit-background-clip: padding-box;
                background-clip: padding-box;
                background-color: #237bbd;
                -moz-box-shadow: 0 2px 3px rgba(0,0,0,.22), inset 0 1px 0 rgba(255,255,255,.2);
                -webkit-box-shadow: 0 2px 3px rgba(0,0,0,.22), inset 0 1px 0 rgba(255,255,255,.2);
                box-shadow: 0 2px 3px rgba(0,0,0,.22), inset 0 1px 0 rgba(255,255,255,.2);
                background-image: url(data:image/svg+xml;base64,PD94bWwgdmVyc2lvbj0iMS4wIiA/Pgo8c3ZnIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIgd2lkdGg9IjEwMCUiIGhlaWdodD0iMTAwJSIgdmlld0JveD0iMCAwIDIwNiA3MiIgcHJlc2VydmVBc3BlY3RSYXRpbz0ibm9uZSI+PGxpbmVhckdyYWRpZW50IGlkPSJoYXQwIiBncmFkaWVudFVuaXRzPSJvYmplY3RCb3VuZGluZ0JveCIgeDE9IjUwJSIgeTE9IjEwMCUiIHgyPSI1MCUiIHkyPSItMS40MjEwODU0NzE1MjAyZS0xNCUiPgo8c3RvcCBvZmZzZXQ9IjAlIiBzdG9wLWNvbG9yPSIjMjM3YmJkIiBzdG9wLW9wYWNpdHk9IjEiLz4KPHN0b3Agb2Zmc2V0PSIxMDAlIiBzdG9wLWNvbG9yPSIjMjk5OWVmIiBzdG9wLW9wYWNpdHk9IjEiLz4KICAgPC9saW5lYXJHcmFkaWVudD4KCjxyZWN0IHg9IjAiIHk9IjAiIHdpZHRoPSIyMDYiIGhlaWdodD0iNzIiIGZpbGw9InVybCgjaGF0MCkiIC8+Cjwvc3ZnPg==);
                background-image: -moz-linear-gradient(bottom, #237bbd 0%, #2999ef 100%);
                background-image: -o-linear-gradient(bottom, #237bbd 0%, #2999ef 100%);
                background-image: -webkit-linear-gradient(bottom, #237bbd 0%, #2999ef 100%);
                background-image: linear-gradient(bottom, #237bbd 0%, #2999ef 100%);
            }
            .grey {
                cursor: pointer;
                margin: 4px;
                padding: 10px;
                border: 1px solid #cacaca;
                -moz-border-radius: 4px;
                -webkit-border-radius: 4px;
                border-radius: 4px;
                -moz-background-clip: padding;
                -webkit-background-clip: padding-box;
                background-clip: padding-box;
                background-color: #f5f5f5;
                -moz-box-shadow: 0 2px 2px rgba(0,0,0,.1);
                -webkit-box-shadow: 0 2px 2px rgba(0,0,0,.1);
                box-shadow: 0 2px 2px rgba(0,0,0,.1);
                background-image: url(data:image/svg+xml;base64,PD94bWwgdmVyc2lvbj0iMS4wIiA/Pgo8c3ZnIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIgd2lkdGg9IjEwMCUiIGhlaWdodD0iMTAwJSIgdmlld0JveD0iMCAwIDEzNCAzOCIgcHJlc2VydmVBc3BlY3RSYXRpbz0ibm9uZSI+PGxpbmVhckdyYWRpZW50IGlkPSJoYXQwIiBncmFkaWVudFVuaXRzPSJvYmplY3RCb3VuZGluZ0JveCIgeDE9IjUwJSIgeTE9IjEwMCUiIHgyPSI1MCUiIHkyPSItMS40MjEwODU0NzE1MjAyZS0xNCUiPgo8c3RvcCBvZmZzZXQ9IjAlIiBzdG9wLWNvbG9yPSIjZDlkOWQ5IiBzdG9wLW9wYWNpdHk9IjEiLz4KPHN0b3Agb2Zmc2V0PSIxMDAlIiBzdG9wLWNvbG9yPSIjZmJmYmZiIiBzdG9wLW9wYWNpdHk9IjEiLz4KICAgPC9saW5lYXJHcmFkaWVudD4KCjxyZWN0IHg9IjAiIHk9IjAiIHdpZHRoPSIxMzQiIGhlaWdodD0iMzgiIGZpbGw9InVybCgjaGF0MCkiIC8+Cjwvc3ZnPg==);
                background-image: -moz-linear-gradient(bottom, #d9d9d9 0%, #fbfbfb 100%);
                background-image: -o-linear-gradient(bottom, #d9d9d9 0%, #fbfbfb 100%);
                background-image: -webkit-linear-gradient(bottom, #d9d9d9 0%, #fbfbfb 100%);
                background-image: linear-gradient(bottom, #d9d9d9 0%, #fbfbfb 100%);
            }
            .grey:hover {
                background-color: #fcfcfc;
                background-image: url(data:image/svg+xml;base64,PD94bWwgdmVyc2lvbj0iMS4wIiA/Pgo8c3ZnIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIgd2lkdGg9IjEwMCUiIGhlaWdodD0iMTAwJSIgdmlld0JveD0iMCAwIDEzNCAzOCIgcHJlc2VydmVBc3BlY3RSYXRpbz0ibm9uZSI+PGxpbmVhckdyYWRpZW50IGlkPSJoYXQwIiBncmFkaWVudFVuaXRzPSJvYmplY3RCb3VuZGluZ0JveCIgeDE9IjUwJSIgeTE9IjEwMCUiIHgyPSI1MCUiIHkyPSItMS40MjEwODU0NzE1MjAyZS0xNCUiPgo8c3RvcCBvZmZzZXQ9IjAlIiBzdG9wLWNvbG9yPSIjZTFlMWUxIiBzdG9wLW9wYWNpdHk9IjEiLz4KPHN0b3Agb2Zmc2V0PSI4NyUiIHN0b3AtY29sb3I9IiNmZGZkZmQiIHN0b3Atb3BhY2l0eT0iMSIvPgo8c3RvcCBvZmZzZXQ9IjEwMCUiIHN0b3AtY29sb3I9IiNmZGZkZmQiIHN0b3Atb3BhY2l0eT0iMSIvPgogICA8L2xpbmVhckdyYWRpZW50PgoKPHJlY3QgeD0iMCIgeT0iMCIgd2lkdGg9IjEzNCIgaGVpZ2h0PSIzOCIgZmlsbD0idXJsKCNoYXQwKSIgLz4KPC9zdmc+);
                background-image: -moz-linear-gradient(bottom, #e1e1e1 0%, #fdfdfd 87.33%, #fdfdfd 100%);
                background-image: -o-linear-gradient(bottom, #e1e1e1 0%, #fdfdfd 87.33%, #fdfdfd 100%);
                background-image: -webkit-linear-gradient(bottom, #e1e1e1 0%, #fdfdfd 87.33%, #fdfdfd 100%);
                background-image: linear-gradient(bottom, #e1e1e1 0%, #fdfdfd 87.33%, #fdfdfd 100%);
            }
            .grey:active {
                -moz-box-shadow: inset 0 0px 0 #fff;
                -webkit-box-shadow: inset 0 0px 0 #fff;
                box-shadow: inset 0 0px 0 #fff;
                background-color: #e1e1e1;
                background-image: url(data:image/svg+xml;base64,PD94bWwgdmVyc2lvbj0iMS4wIiA/Pgo8c3ZnIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIgd2lkdGg9IjEwMCUiIGhlaWdodD0iMTAwJSIgdmlld0JveD0iMCAwIDEzNCAzOCIgcHJlc2VydmVBc3BlY3RSYXRpbz0ibm9uZSI+PGxpbmVhckdyYWRpZW50IGlkPSJoYXQwIiBncmFkaWVudFVuaXRzPSJvYmplY3RCb3VuZGluZ0JveCIgeDE9IjUwJSIgeTE9IjEwMCUiIHgyPSI1MCUiIHkyPSItMS40MjEwODU0NzE1MjAyZS0xNCUiPgo8c3RvcCBvZmZzZXQ9IjAlIiBzdG9wLWNvbG9yPSIjZmJmYmZiIiBzdG9wLW9wYWNpdHk9IjEiLz4KPHN0b3Agb2Zmc2V0PSIxMDAlIiBzdG9wLWNvbG9yPSIjZGZkZmRmIiBzdG9wLW9wYWNpdHk9IjEiLz4KICAgPC9saW5lYXJHcmFkaWVudD4KCjxyZWN0IHg9IjAiIHk9IjAiIHdpZHRoPSIxMzQiIGhlaWdodD0iMzgiIGZpbGw9InVybCgjaGF0MCkiIC8+Cjwvc3ZnPg==);
                background-image: -moz-linear-gradient(bottom, #fbfbfb 0%, #dfdfdf 100%);
                background-image: -o-linear-gradient(bottom, #fbfbfb 0%, #dfdfdf 100%);
                background-image: -webkit-linear-gradient(bottom, #fbfbfb 0%, #dfdfdf 100%);
                background-image: linear-gradient(bottom, #fbfbfb 0%, #dfdfdf 100%);
            }
            .blue:hover {
                background-color: #2985cb;
                -moz-box-shadow: 0 2px 3px rgba(0,0,0,.22), inset 0 1px 0 rgba(255,255,255,.2);
                -webkit-box-shadow: 0 2px 3px rgba(0,0,0,.22), inset 0 1px 0 rgba(255,255,255,.2);
                box-shadow: 0 2px 3px rgba(0,0,0,.22), inset 0 1px 0 rgba(255,255,255,.2);
                background-image: url(data:image/svg+xml;base64,PD94bWwgdmVyc2lvbj0iMS4wIiA/Pgo8c3ZnIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIgd2lkdGg9IjEwMCUiIGhlaWdodD0iMTAwJSIgdmlld0JveD0iMCAwIDIwNiA3MiIgcHJlc2VydmVBc3BlY3RSYXRpbz0ibm9uZSI+PGxpbmVhckdyYWRpZW50IGlkPSJoYXQwIiBncmFkaWVudFVuaXRzPSJvYmplY3RCb3VuZGluZ0JveCIgeDE9IjUwJSIgeTE9IjEwMCUiIHgyPSI1MCUiIHkyPSItMS40MjEwODU0NzE1MjAyZS0xNCUiPgo8c3RvcCBvZmZzZXQ9IjAlIiBzdG9wLWNvbG9yPSIjMjk4NWNiIiBzdG9wLW9wYWNpdHk9IjEiLz4KPHN0b3Agb2Zmc2V0PSIxMDAlIiBzdG9wLWNvbG9yPSIjNGZhYmYzIiBzdG9wLW9wYWNpdHk9IjEiLz4KICAgPC9saW5lYXJHcmFkaWVudD4KCjxyZWN0IHg9IjAiIHk9IjAiIHdpZHRoPSIyMDYiIGhlaWdodD0iNzIiIGZpbGw9InVybCgjaGF0MCkiIC8+Cjwvc3ZnPg==);
                background-image: -moz-linear-gradient(bottom, #2985cb 0%, #4fabf3 100%);
                background-image: -o-linear-gradient(bottom, #2985cb 0%, #4fabf3 100%);
                background-image: -webkit-linear-gradient(bottom, #2985cb 0%, #4fabf3 100%);
                background-image: linear-gradient(bottom, #2985cb 0%, #4fabf3 100%);
            }
            .ct {
                width:450px;
                margin:0 auto;
            }
            .row {
                width:450px;
            }
            .row .element {
                width:150px;
                text-align:left;
                float:left;
            }
            .row .strong {
                width:300px;
                font-weight:bold;
                float:left;
            }
            .row .input {
                width:300px;
                float:left;
            }
            .row .input input {
                width:300px;
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
                background-image: url(data:image/svg+xml;base64,PD94bWwgdmVyc2lvbj0iMS4wIiA/Pgo8c3ZnIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIgd2lkdGg9IjEwMCUiIGhlaWdodD0iMTAwJSIgdmlld0JveD0iMCAwIDI5NyAyOSIgcHJlc2VydmVBc3BlY3RSYXRpbz0ibm9uZSI+PGxpbmVhckdyYWRpZW50IGlkPSJoYXQwIiBncmFkaWVudFVuaXRzPSJvYmplY3RCb3VuZGluZ0JveCIgeDE9IjUwJSIgeTE9IjEwMCUiIHgyPSI1MCUiIHkyPSItMS40MjEwODU0NzE1MjAyZS0xNCUiPgo8c3RvcCBvZmZzZXQ9IjAlIiBzdG9wLWNvbG9yPSIjZmZmIiBzdG9wLW9wYWNpdHk9IjEiLz4KPHN0b3Agb2Zmc2V0PSIxMDAlIiBzdG9wLWNvbG9yPSIjZjdmN2Y3IiBzdG9wLW9wYWNpdHk9IjEiLz4K                ICAgPC9saW5lYXJHcmFkaWVudD4KCjxyZWN0IHg9IjAiIHk9IjAiIHdpZHRoPSIyOTciIGhlaWdodD0iMjkiIGZpbGw9InVybCgjaGF0MCkiIC8+Cjwvc3ZnPg==); /* gradient overlay */
                background-image: -moz-linear-gradient(bottom, #fff 0%, #f7f7f7 100%); /* gradient overlay */
                background-image: -o-linear-gradient(bottom, #fff 0%, #f7f7f7 100%); /* gradient overlay */
                background-image: -webkit-linear-gradient(bottom, #fff 0%, #f7f7f7 100%); /* gradient overlay */
                background-image: linear-gradient(bottom, #fff 0%, #f7f7f7 100%); /* gradient overlay */
    
            }
            .error {color:maroon;}
            .center {text-align:center;}
        </style>
        <script type="text/javascript">
            window.resizeTo(655, document.height);
        </script>
    </head>
    <body>
        % if action == 'denied':
            <p class="error">
                {{_('Access denied')}}
            </p>
            <div class="center">
                <button class="grey" onclick="window.close();">
                    {{_("Close window")}}
                </button>
            </div>

        % elif action == 'logged_in':
            <p class="error">{{_("You are already logged in as {username} on {machine_name} ({os_name})").format(username=username, machine_name=machine_name, os_name=os_name)}}</p>
            <div class="center">
                <button class="grey" onclick="window.close();">
                    {{_("Close window")}}
                </button>
            </div>

        % elif action == 'close':
            <p class="error">
                {{_("Account change on client {username} on {machine_name} ({os_name}) in progress").format(username=username, machine_name=machine_name, os_name=os_name)}}
            </p>
            <div class="center">
            <button class="grey" onclick="window.close();">
                {{_("Close window")}}
            </button>
            </div>
            <script type="text/javascript">window.close();</script>

        % else:
            <p style="margin-top:40px;">
                {{_("You reached the Download.am Client on {machine_name} ({os_name})").format(username=username, machine_name=machine_name, os_name=os_name)}}<br />
                {{_("Would you like to login now?")}}
            </p>
            <div class="ct">
                <form method="post" action="{{login_url}}">
                    <div class="row">
                        <div class="element">{{_("Username:")}}</div>
                        <div class="strong">{{username}}</div>
                    </div>
                    <div class="row">
                        <div class="element">{{_("Password:")}}</div>
                        <div class="input"><input type="password" name="password" /></div>
                    </div>
                    <div style="clear:both;"></div>
                    <div class="buttons">
                        <button type="submit" class="blue">
                            {{_("Login")}}
                        </button>
                        <button class="grey" onclick="window.close();">
                            {{_("Cancel")}}
                        </button>
                    </div>
                </form>
            </div>
    </body>
</html>
""")
