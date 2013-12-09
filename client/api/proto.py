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

import sys
import zlib
import time
import json
import base64
import traceback
from functools import partial

from .. import logger, login, interface, settings

log = logger.get("api")

VERSION = 1

COMPRESSED = 0x01
ENCRYPTED = 0x02
BASE64 = 0x04

debug = True


class ProtoResponder(object):
    def __init__(self, send_message, source, command, in_response_to, channel):
        self.send_message = send_message
        self.source = source
        self.command = command
        self.in_response_to = in_response_to
        self.channel = channel

    def send(self, payload=None, command=None):
        command = command if command is not None else self.command
        #print command, payload
        message = pack_message(self.source, command, self.in_response_to, payload, self.channel)
        self.send_message(message)


def deflate(data, compresslevel=9):
    compress = zlib.compressobj(compresslevel, zlib.DEFLATED, -zlib.MAX_WBITS, zlib.DEF_MEM_LEVEL, 0)
    deflated = compress.compress(data)
    deflated += compress.flush()
    return deflated


def inflate(data):
    return zlib.decompress(data, -zlib.MAX_WBITS)


def get_type(type):
    if type in ('frontend', 'backend'):
        return type, settings.app_uuid
    if type == 'client':
        raise RuntimeError('type client is forbidden here')
    return 'client', type


def pack_message(destination, command=None, in_response_to=None, payload=None, channel=None, encrypt=True):
    # source = origin
    # destination = type
    # layer 1 (transport, plain): [version, channel, source, destination, in_response_to, flags, LAYER_2]
    # layer 2 (data, encrypted): [timestamp, command, PAYLOAD]
    if encrypt:
        if encrypt == "rsa":
            assert destination == "backend"
            encrypt = login.pub_key.encrypt
        elif encrypt == "rsa2":
            assert destination == "backend"
            encrypt = login.pub_key2.encrypt
        else:
            encrypt = partial(login.encrypt, destination)
    layer1 = [
        VERSION,
        settings.app_uuid if channel is None else channel,
        'client',
        destination,
        in_response_to,
        0,
        json.dumps([int(time.time()*1000), command, payload])
    ]
    if debug:
        log.debug('SEND: {}'.format(layer1))
    if len(layer1[6]) > 300:
        layer1[5] |= COMPRESSED
        layer1[6] = deflate(layer1[6])
    if encrypt:
        layer1[5] |= ENCRYPTED
        layer1[6] = encrypt(layer1[6])
    layer1[5] |= BASE64
    layer1[6] = base64.standard_b64encode(layer1[6])

    return layer1


def unpack_message(layer1):
    if isinstance(layer1, basestring):
        layer1 = json.loads(layer1)
    if layer1[5] & BASE64:
        layer1[6] = base64.standard_b64decode(str(layer1[6]).replace('\n', ''))
    if layer1[5] & ENCRYPTED:
        layer1[6] = login.decrypt(layer1[2], layer1[6])
    if layer1[5] & COMPRESSED:
        layer1[6] = inflate(layer1[6])
    if isinstance(layer1[6], basestring):
        layer1[6] = json.loads(layer1[6])

    layer1[5] = 0
    if debug:
        log.debug('RECV: {}'.format(layer1))
    return layer1


def process_message(send_message, layer1):
    channel = layer1[1]
    source = layer1[2]
    in_response_to = layer1[4]
    command = layer1[6][1]
    arguments = layer1[6][2] or dict()

    responder = ProtoResponder(send_message, source, command, in_response_to, channel)
    try:
        module, funcname = command.rsplit(".", 1)
        payload = interface.call(module, funcname, responder, **arguments)
        if payload is not None:
            responder.send(payload)
    except:
        e = sys.exc_info()[1]
        payload = {
            "exception": e.__class__.__name__,
            "message": getattr(e, "message", None),
            "traceback": traceback.format_exc(),
            "response": True,
        }
        responder.send(payload)
        log.unhandled_exception('in process_message')


def handle_message(send_message, data):
    data = unpack_message(data)
    process_message(send_message, data)


client = None
connections = list()


def add_connection(connection):
    if connection not in connections:
        connections.append(connection)


def remove_connection(connection):
    if connection in connections:
        connections.remove(connection)


def send(destination, command=None, in_response_to=None, payload=None, channel=None, encrypt=True, _wait_for_master_connection=False):
    """default routing function for global messages
    """
    if client is None:
        return
    message = pack_message(destination, command, in_response_to, payload, channel, encrypt)
    if _wait_for_master_connection:
        client.wait_connected()
    if client.is_connected():
        client.send_message(message)
    if destination == 'frontend':
        for connection in connections:
            connection.send_message(message)
