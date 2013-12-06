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

import json
import traceback
import random

import gevent
from gevent.event import Event
#import websocket

from . import client as oldclient
from ..contrib import websocket
from .. import logger, settings, event

log = logger.get("api.client2")


class NewAPIClient(oldclient.APIClient):
    nodes = ["81.95.0.18:4224"]
    use_rsa = "rsa2"

    def connect(self):
        try:
            try:
                self.io.close()
            except AttributeError:
                pass
            node = self.node_cycler.next()
            #node_port = self.node_port

            log.info('sockjs connecting to {}'.format(node))

            def on_open(io):
                self.disconnected_event.clear()
            _connected = Event()

            def on_message(io, msg):
                if msg == "o":
                    _connected.set()
                    self.io.connected = True
                    log.info("sockjs connected")
                elif msg == "PING":
                    print "PONG PONG"
                    log.info("PONG PONG")
                elif msg == "c":
                    self.io.connected = False
                    self.io.sock.settimeout(60)
                    log.info("sockjs about to close")
                elif msg == "h":
                    print("received heartbeat")
                elif msg.startswith("a"):
                    for m in json.loads(msg[1:]):
                        self.on_message(json.loads(m))
                else:
                    print "unknown message type"

            def on_error(io, e):
                log.error("ERROR sockjs, {}".format(e))
                self.disconnected_event.set()
                raise e

            def on_close(io):
                log.error("SOCKJS CONNECTION closed")
                self.disconnected_event.set()
                loop.kill()

            url = "ws://{}/backend/{}/{}/websocket".format(
                  node, random.randint(100, 999), settings.app_uuid)
            self.io = websocket.WebSocketApp(
                url,
                on_open=on_open, on_message=on_message, on_error=on_error,
                on_close=on_close)
            loop = gevent.spawn(self.io.run_forever)
            _connected.wait()

        except (KeyboardInterrupt, SystemExit, gevent.GreenletExit):
            raise
        except BaseException as e:
            traceback.print_exc()
            return e

    def close(self):
        try:
            self.io.close()
            oldclient.APIClient.close(self)
        except AttributeError:
            pass

    def send_message(self, message):
        self.io.send('[' + json.dumps(json.dumps(message)) + ']')

    def emit(self, *args):
        pass
        #print "DROP:", args


client = NewAPIClient()


@event.register('login:changed')
@event.register('proxy:changed')
def _(e, *args):
    client.disconnected_event.set()


@event.register('reconnect:reconnecting')
def __(e, *args):
    client.disconnected_event.set()
