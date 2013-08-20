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

import re
import bisect

from ..plugintools import Url, Matcher, all_url_regex

plugins = []
index = dict()

def add(module):
    plugin = module.this.model(module)
    bisect.insort(plugins, (plugin.priority, 0, plugin))

def find(url, ignore=None):
    if ignore is None:
        ignore = set()
    _url = Url(url)
    for p in plugins:
        plugin = p[2]
        if not plugin.name in ignore and not plugin.multihoster:
            assert plugin.name is not None
            for pattern in plugin.patterns:
                if isinstance(pattern, Matcher):
                    pmatch = pattern.match(_url)
                else:
                    pmatch = pattern.match(url)
                if pmatch is not None:
                    if p[1] == 0:
                        plugins.remove(p)
                        bisect.insort(plugins, (plugin.priority, -1, plugin))
                    return plugin, pmatch

def find_by_name(name, default=None):
    if len(index) != len(plugins):
        index.clear()
        for _, _, plugin in plugins:
            index[plugin.name] = plugin
    try:
        return index[name]
    except KeyError:
        if default:
            return index.get(default)
        else:
            raise

def collect_links(text, schemeless=True):
    links = set([])
    if not text:
        return
    for m in all_url_regex.finditer(text):
        links.add(m.group('url'))

    # TODO: this is a big performance bug
    if schemeless and False:
        for host in plugins:
            for p in host[2].patterns:
                if not hasattr(p, 'get_regex'):
                    continue
                r = p.get_regex(False)
                if r is None:
                    continue
                mm = r.finditer(text)
                for m in mm:
                    if '://' in m.group('url'):
                        continue
                    type = None
                    for scheme in p.scheme:
                        if type is None:
                            if 'http' in scheme.pattern:
                                type = 'http'
                            elif 'ftp' in scheme.pattern:
                                type = 'ftp'
                        r = re.compile(scheme.pattern+'://'+re.escape(m.group('url')))
                        if any(r.match(link) for link in links):
                            break
                    else:
                        if type is not None:
                            link = type+'://'+m.group('url')
                            links.add(link)
    return links
