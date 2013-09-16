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

import os
import io
import time
import base64
import json
import bisect
import traceback
import gevent

from contextlib import closing
from PIL import Image, ImageFilter, ImageDraw, ImageFont
import requests

from bs4 import BeautifulSoup

from gevent.pool import Group
from gevent.event import Event
from gevent.lock import Semaphore
from gevent.local import local
from gevent import Timeout

from ..contrib import contentdisposition
from ..contrib.Win32IconImagePlugin import _accept as is_ico, Win32IcoFile
from .. import plugintools, account, logger, input, interface, settings
from ..config import globalconfig
from ..scheme import transaction

log = logger.get("hoster.util")
localctx = local()

def _get_content_range_bytes(range):
    bytes = range.split(' ', 1)[1]
    a = bytes.split('-')
    b = a[1].split('/')
    return int(a[0]), int(b[0]), int(b[1])

def http_response_errors(ctx, resp):
    if resp.status_code == 416:
        raise plugintools.RangeNotSatisfiedError()
        #ctx.fatal('got HTTP 416 - Requested range not satisfiable')
    elif resp.status_code == 503:
        raise plugintools.NoMoreConnectionsError()
    elif resp.status_code == 401:
        ctx.fatal('401 Not Authorized')
    elif resp.status_code == 404:
        ctx.fatal('404 Not Found')
    elif resp.status_code in (400, 402, 403, 405, 406, 407, 408):
        ctx.fatal('40x error: {}'.format(resp.status_code))
    elif resp.status_code in (500, 501, 502, 504, 505, 506, 507, 508):
        ctx.retry('server error (HTTP 50x response)', 120)
    elif not resp.status_code in (200, 206):
        ctx.fatal('need status code 200 or 206, got {}'.format(resp.status_code))

def http_response(chunk, resp=None):
    """helper function. returns name, size"""
    if resp is None:
        close = True
        resp = chunk.account.get(chunk.url, stream=True)
    else:
        close = False
    http_response_errors(chunk, resp)

    if resp.headers.get('Content-Length', None):
        length = int(resp.headers['Content-Length']) + chunk.pos
    else:
        length = None

    if resp.headers.get('Content-Disposition', None) not in (None, 'attachment'):
        name = contentdisposition.parse(resp.headers['Content-Disposition'])
    else:
        name = None

    if resp.headers.get('Accept-Ranges', None) and 'bytes' in resp.headers['Accept-Ranges']:
        can_resume = True
    else:
        can_resume = False

    # when content-range is in response we CAN resume
    if resp.headers.get('Content-Range', None) and 'bytes' in resp.headers['Content-Range']:
        current_pos, end, size = _get_content_range_bytes(resp.headers['Content-Range'])
        can_resume = True
    else:
        current_pos = 0
        size = length

    # check chunk and http positions
    if current_pos != chunk.pos:
        #chunk.retry('requested position {} not satisfied. got Content-Range {}, current_pos {}'.format(chunk.pos, resp.headers.get('Content-Range', None), current_pos), 90)
        chunk.log.warning('requested position {} not satisfied (got {}). Content-Range: {}'.format(chunk.pos, current_pos, resp.headers.get('Content-Range', None)))
        can_resume = False
    
    if close:
        resp.close()

    return name, length, size, can_resume
    
def check_download_url(file, *args, **kwargs):
    kwargs["stream"] = True
    name = kwargs.pop("name", None)
    need_content_disposition = kwargs.pop('need_content_disposition', False)
    with closing(file.account.get(*args, **kwargs)) as resp:
        resp.raise_for_status()
        if need_content_disposition and 'Content-Disposition' not in resp.headers:
            file.no_download_link()
        if not name:
            if resp.headers.get('Content-Disposition', None) not in (None, 'attachment'):
                name = contentdisposition.parse(resp.headers['Content-Disposition'])
            else:
                if file.name is None:
                    name = plugintools.auto_generate_filename(file)
                    file.log.warning('auto generated filename: {}'.format(name))
        file.set_infos(size=int(resp.headers.get('Content-Length', 0)), name=name)
        return resp
    
def serialize_html_form(form):
    attrs = form.find_all('input')
    attrs += form.find_all('button')
    attrs += form.find_all('select')
    result = dict()
    for attr in attrs:
        name = attr.get("name")
        if name:
            result[attr.get('name')] = attr.get('value')
    for attr in form.find_all('textarea'):
        name = attr.get("name")
        if name:
            result[name] = attr.text
    return form.get("action"), result

def get_multihoster_account(task, multi_match, file):
    if not account.config.use_useraccounts:
        return
    
    group = Group()
    for pool in account.manager.values():
        for acc in pool:
            if acc.multi_account:
                from . import manager
                acc.hoster = manager.find_by_name(acc.name)
                group.spawn(acc.boot)
    group.join()

    accounts = []
    best_weight = 0
    hostname = file.split_url.host
    for pool in account.manager.values():
        for acc in pool:
            if acc._private_account:
                continue
            if not acc.multi_account:
                continue
            if hasattr(acc, 'premium') and not acc.premium:
                continue
            if not multi_match(acc, hostname):
                continue
            try:
                weight = acc.weight
            except gevent.GreenletExit:
                print "greenlet exit"
                continue
            if weight > best_weight:
                accounts = []
                best_weight = weight
            bisect.insort(accounts, (acc.get_task_pool(task).full() and 1 or 0, len(accounts), acc))
    if accounts:
        return accounts[0][2]
        """try:
            file.log.info('trying multihoster {}'.format(acc.name))
            acc.hoster.get_download_context(file)
        except gevent.GreenletExit:
            raise
        except BaseException as e:
            log.exception(e)"""
    else:
        print "multi: no accounts found"

######## premium accounts...

asked = dict()
premium_lock = Semaphore()

config = globalconfig.new('hoster_util')
config.default('ignore_ask_premium', list(), list)

def buy_premium(hoster, url):
    with premium_lock:
        if not account.config.ask_buy_premium:
            return
        try:
            if isinstance(asked[hoster], Event):
                asked[hoster].wait()
            if asked[hoster] > (time.time() - account.config.ask_buy_premium_time):
                return
        except KeyError:
            pass
        asked[hoster] = Event()
        ignore_asked = False
        try:
            return ask_buy_premium_dialog(hoster, url)
        except gevent.GreenletExit:
            ignore_asked = True
            raise
        finally:
            e = asked[hoster]
            if ignore_asked:
                del asked[hoster]
            else:
                asked[hoster] = time.time()
            e.set()

def ask_buy_premium_dialog(hoster, url):
    if hoster in config.ignore_ask_premium:
        return
    try:
        elements = [
            input.Text("Buy premium account for hoster {}?".format(hoster)),
            input.Choice('answer', choices=[
                {"value": 'yes', "content": "Yes", "link": url},
                {"value": 'no', "content": "No"},
                {"value": 'already', "content": "I already have an account"},
                {"value": 'never', "content": "No and never ask again"}
            ]),
        ]
        result = input.get(elements)
        if result['answer'] == 'already':
            elements = [
                input.Text('Please enter your account details for {}'.format(hoster)),
                input.Float('left'),
                [input.Text('Username:'), input.Input('username')],
                [input.Text('Password:'), input.Input('password', 'password')],
                input.Float('center'),
                input.Text(''),
                input.Choice('answer', choices=[
                    {"value": 'add', "content": "Add Account", 'ok': True},
                    {"value": 'cancel', "content": "Cancel", 'cancel': True}
                ])
            ]
            result = input.get(elements, close_aborts=True)
            if result['answer'] == 'add' and result['username'] and result['password']:
                interface.call('account', 'add', name=hoster, username=result['username'], password=result['password'])
                raise gevent.GreenletExit()
        elif result['answer'] == 'never':
            if hoster not in config.ignore_ask_premium:
                with transaction:
                    config.ignore_ask_premium.append(hoster)
    except KeyError:
        pass
    except input.InputTimeout:
        log.warning('input timed out')
    except input.InputError:
        log.error('input was aborted')

def add_extra(url, data):
    return url + "&---extra=" + base64.urlsafe_b64encode(json.dumps(data))

def convert_icon(data, size, module=None):
    try:
        if is_ico(data): # explictly use included ico plugin, dont rely on PIL to pick the right one...
            img = Win32IcoFile(io.BytesIO(data)).get_image((size, size))
        else:
            img = Image.open(io.BytesIO(data))
    except:
        traceback.print_exc()
        l = module.log if module else log
        l.exception("Cannot open favicon for {}".format(module.name if module else '<unknown>'))
        return None
    newimg = img.resize((size, size), Image.ANTIALIAS)
    try:
        newimg.filter(ImageFilter.EDGE_ENHANCE)
    except:
        pass
    out = io.BytesIO()
    newimg.save(out, "PNG", **img.info)
    return base64.b64encode(out.getvalue())

_timeouted_icons = dict()

def _safe_get(url):
    if url in _timeouted_icons and time.time() - _timeouted_icons[url] < 300:
        return
    try:
        with Timeout(10):
            resp = requests.get(url, stream=True)
        resp.raise_for_status()
    except Timeout:
        _timeouted_icons[url] = time.time()
        return
    except RuntimeError:
        pass
    else:
        if int(resp.headers.get("content-size", 0)) < 50*1024:
            return resp

def find_favicon(hostname=None, url=None):
    assert hostname or url
    if not url:
        url = "http://{}/favicon.ico".format(hostname)
    data = _safe_get(url)
    if data:
        return data.content
    if not hostname:
        return
    url = "http://{}/".format(hostname)
    data = _safe_get(url)
    if data and data.headers.get("Content-Type", "") == "text/html":
        soup = BeautifulSoup(data.text)
        url = soup.find("link", rel="shortcut icon")
        if url:
            url = url.get("href")
            if url:
                resp = _safe_get(url)
                if resp:
                    return resp.content

def generate_icon(text, textcolor=(0, 0, 0)):
    ttpath = os.path.join(settings.bin_dir, "SourceCodePro-Bold.ttf")
    try:
        font = ImageFont.truetype(ttpath, 100)
    except IOError:
        raise
    size = font.getsize(text)
    size = max((size[0]+20, size[1]))
    image = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(image)
    draw.text((10, 4), text, textcolor, font=font)
    data = io.BytesIO()
    image.save(data, "PNG")
    return base64.b64encode(data.getvalue())

def get_image(*args, **kwargs):
    """get an image from url, arguemnts get passed to requests.get, returns Image object, raises RuntimeError if something goes wrong"""
    try:
        data = requests.get(*args, **kwargs)
        data.raise_for_status()
        return Image.open(io.BytesIO(data.content))
    except RuntimeError:
        raise
    except:
        traceback.print_exc()
        raise RuntimeError("Could not load or open Image")
    
def parse_seconds(s):
    """parses the seconds from strings like 01:47:35
    """
    t = 0.0
    try:
        s, ms = s.split('.', 1)
        if ms:
            t += float(ms)/1000
    except ValueError:
        pass
    times = [1, 60, 3600, 86400]
    for i, s in enumerate(reversed(s.split(':'))):
        t += float(s)*times[i]
    return t

def parse_seconds2(s):
    """parses the seconds from strings like 2 hours 1 minute 50 seconds
    """
    wait = 0
    t = dict(hour=3600, hours=3600, minute=60, minutes=60, second=1, seconds=1)
    for x in s.split(', '):
        a, b = x.split(' ', 1)
        wait += int(a.strip())*t[b.strip()]
    return wait

def xfilesharing_download(resp, step=1, free=True):
    form = resp.soup.find('input', attrs=dict(name='op', value='download{}'.format(step)))
    form = form.find_parent('form')

    # remove child forms (yes, they exists sometimes)
    for f in form.find_all('form'):
        f.decompose()
    
    action, data = serialize_html_form(form)
    if free:
        data.pop('method_premium', None)
    else:
        data.pop('method_free', None)
    return lambda *args, **kwargs: resp.post(action, data=data, *args, **kwargs), data
