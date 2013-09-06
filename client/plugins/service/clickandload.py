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
import socket
import webbrowser

from bottle import Bottle, request, redirect
import gevent
from gevent import pywsgi

from ... import service, core, container, localrpc, login, event

cnl = Bottle()

@cnl.route("/crossdomain.xml")
def crossdomain():
    return """<?xml version="1.0"?>
<!DOCTYPE cross-domain-policy SYSTEM "http://www.macromedia.com/xml/dtds/cross-domain-policy.dtd">
<cross-domain-policy>
<allow-access-from domain="*" />
</cross-domain-policy>"""

@cnl.route("/flash")
@cnl.route("/flash", method="POST")
@cnl.route("/alive")
def flash():
    return "JDownloader\r\n"

@cnl.route("/jdcheck.js")
def jdcheck():
    return "jdownloader=true;\nvar version='9.581';\n"

@cnl.post("/flash/add")
def add():
    urls = filter(bool, request.forms["urls"].splitlines())
    if not valid(urls):
        return "client declined\r\n"
    core.add_links(urls)
    return "success\r\n"

@cnl.post("/flash/addcrypted")
def addcrypted():
    dlc = request.forms["crypted"].replace(" ", "+")
    urls = container.decrypt_dlc(dlc)
    if not valid(urls):
        return "client declined\r\n"
    core.add_links(urls)
    redirect(login.get_sso_url('collect'))
    return "success\r\n"

@cnl.post("/flash/addcrypted2")
def addcrypted2():
    urls = container.decrypt_clickandload(request.forms)
    if not valid(urls):
        return "client declined\r\n"
    core.add_links(urls)
    redirect(login.get_sso_url('collect'))
    return "success\r\n"
    
    
_block_to = 0
def valid(urls):
    global _block_to
    if _block_to > time.time():
        return False
    _block_to = time.time() + cnl_service.config.add_block_for
    if _dialog_open:
        return False
    return add_dialog(len(urls))

    
_dialog_open = False
def add_dialog(count):
    global _dialog_open
    try:
        from ... import input
        if cnl_service.config.add:
            answer = cnl_service.config.add
        else:
            try:
                elements = list()
                elements.append(input.Text(["An external website wants to add #{count} links.", dict(count=count)]))
                elements.append(input.Input('always_add', 'checkbox', default=False, label='Always add links without asking.'))
                elements.append(
                    input.Choice('answer', choices=[
                        {"value": "add", "content": "Add"},
                        {"value": "add_open", "content": "Add and open browser"},
                        {"value": "discard", "content": "Discard once"}
                    ]))
                result = input.get(elements, type='remember_boolean')
            except input.InputAborted:
                answer = 'discard'
            except input.InputTimeout:
                answer = 'add'
            else:
                answer = result.get("answer", "discard")
                if result.get('always_add', False):
                    with core.transaction:
                        cnl_service.config.add = answer
        if answer == 'add_open':
            webbrowser.open_new_tab(login.get_sso_url('collect'))
        return answer in ('add', 'add_open')
    finally:
        _dialog_open = False

class ClickAndLoad(service.ServicePlugin):
    server = None
    default_enabled = True
    
    def __init__(self, name):
        service.ServicePlugin.__init__(self, name)
        self.config.default("add", None, str, description="Always add the links without asking.")
        if self.config.add in ('True', 'False'):
            self.config.add = None
        self.config.default("add_block_for", 5, int, description="Block the clickandload feature for this amount of seconds.")
        if not self.config.enabled: # TODO: remove this and make config setting on website
            self.config.enabled = True

    def stop(self):
        if self.server:
            try:
                self.server.stop()
            except:
                self.log.excecption("error while stopping")
            self.server = None

    def run(self):
        log = 0
        self.stop()
        while True:
            try:
                self.server = pywsgi.WSGIServer(("127.0.0.1", 9666), cnl)
                self.server.start()
                localrpc.prevent_socket_inheritance(self.server.socket)
                self.server.serve_forever()
            except socket.error:
                if not log:
                    self.log.exception('port 9666 for click and load is already taken!')
                    log = 20
                log -= 1
                gevent.sleep(5)

cnl_service = ClickAndLoad('click_and_load')
service.register(cnl_service)
