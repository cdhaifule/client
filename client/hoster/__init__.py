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

import os
import sys
import urlparse
import shelve
import time
import random
import traceback
import io
from HTMLParser import HTMLParser
from gevent import pool
from bs4 import BeautifulSoup as Soup

from PIL import Image

from . import util
from .. import interface, logger, plugintools
from ..contrib import sizetools
from ..plugintools import Url, Matcher, regexp, wildcard, between
from ..contrib import contentdisposition
from .manager import add as register, find, find_by_name, collect_links
from . import manager, search
from .this import host
from .models import Hoster, HttpHoster, PremiumHoster, HttpPremiumHoster, \
    MultiHoster, MultiHttpHoster, MultiHttpPremiumHoster, cfg
from .util import http_response_errors, _get_content_range_bytes, \
    http_response, serialize_html_form, get_multihoster_account, localctx, \
    buy_premium, check_download_url, find_favicon, generate_icon, add_extra, \
    parse_seconds, get_image

from ..scheme import transaction
from ..settings import temp_dir

# helper functions
BYTES = sizetools.BYTES
KB = sizetools.KB
MB = sizetools.MB
GB = sizetools.GB
TB = sizetools.TB
PB = sizetools.PB
urlsplit = urlparse.urlsplit
urljoin = urlparse.urljoin
htmlunescape = HTMLParser().unescape

log = logger.get('hoster')

cache_valid_for = 1 * 7 * 24 * 60 * 60 # 1 week min
cache_vary = [cache_valid_for, cache_valid_for*2] # vary caching duration between 1 and 2 weeks

@interface.register
class HosterInterface(interface.Interface):
    name = "hoster"

    def list_plugins():
        """lists all plugins"""
        return [dict(name=plugin.name,
                     alias=plugin.alias,
                     search=plugin.search,
                     membership=hasattr(plugin.accounts.account_class, 'username'))
                for _, __, plugin in manager.plugins]

    def load_icons(names=None, size=16):
        if icon_cache is None:
            return {}
        try:
            size = int(size)
        except TypeError:
            return {}
        if not isinstance(names, list):
            names = [names]
        #print "icon request:", names, size
        now = time.time()

        def _load(name):
            if isinstance(name, unicode):
                name = name.encode("utf-8")
            d = None
            sizes = dict()
            try:
                if name in icon_cache and icon_cache[name][0] > now:
                    sizes = icon_cache[name][1]
                    d = sizes.get(0, None)
                    #print "icon_cache hit:", name, size
                    return name, icon_cache[name][1][size]
            except KeyError:
                pass
            except IndexError:
                reset_icon_cache()
            hoster = manager.find_by_name(name, default="http")
            print "Loadicon found plugin:", hoster.name
            sizes = {}
            if not d:
                print "load icon", name
                d = hoster.load_icon(name)
                if isinstance(d, Image.Image):
                    i = io.BytesIO()
                    d.save(i, "PNG")
                    d = i.getvalue()
                if not d:
                    print "plugin returned no image!"
            if d:
                data = util.convert_icon(d, size, hoster)
                sizes[size] = data
                sizes[0] = d
                icon_cache[name] = (now + random.randint(*cache_vary), sizes)
                return name, data
            else:
                return name, False
        
        data = {"{},{}".format(name, size): data for name, data in pool.IMapUnordered.spawn(_load, names, pool.Pool(20).spawn)}
        icon_cache.sync()
        return data
        
    def search(id=None, search_id=None, plugins=None, query=None, tags=["other"], max_results=50, responder=None):
        return search.search(responder, id, search_id, plugins, query, tags, max_results)

    def search_more(id=None, search_id=None, max_results=50, responder=None):
        return search.search_more(responder, id, search_id, max_results)

def reset_icon_cache():
    global icon_cache
    if icon_cache is not None:
        icon_cache.close()
    os.remove(icon_cache_path)
    icon_cache = shelve.open(icon_cache_path.encode(sys.getfilesystemencoding()), writeback=True)
    icon_cache.close = icon_cache.sync

icon_cache = None
icon_cache_path = os.path.join(temp_dir, "icon_cache")

def check_dependencies(module, retry):
    if hasattr(module.this, 'uses'):
        uses = module.this.uses
        depends = [uses] if isinstance(uses, basestring) else uses
        for name in depends:
            try:
                manager.find_by_name(name)
            except KeyError:
                retry.append(module)
                return True

def init():
    global icon_cache
    
    # load icon cache
    icon_cache = shelve.open(icon_cache_path.encode(sys.getfilesystemencoding()), writeback=True)
    icon_cache.close = icon_cache.sync
    try:
        a, (b, c) = icon_cache.iteritems().next() # reset cache if format not correct.
        b/1
        assert isinstance(c, dict)
    except StopIteration:
        pass
    except:
        print "resetting icon cache...", traceback.format_exc()
        reset_icon_cache()
    
    # load modules
    retry = list()
    from . import jshoster
    for module in plugintools.itermodules('hoster', handlers=dict(js=jshoster.load)):
        if not hasattr(module, 'this'):
            log.warning('found no this object in module {}'.format(module.__name__))
            continue

        # check if there are dependencies that needs to be loaded before this module
        if check_dependencies(module, retry):
            continue

        register(module)

    # solve dependencies
    while retry:
        changed = False
        next_retry = list()
        for module in retry:
            if check_dependencies(module, next_retry):
                continue
            register(module)
            changed = True
        retry = next_retry
        if not changed:
            break

    # retry and fail all unsolved dependencies
    for module in retry:
        try:
            register(module)
        except BaseException as e:
            log.exception('error initializing module {}: {}'.format(module.this.name, e))

def terminate():
    try:
        icon_cache.sync()
    except:
        pass
