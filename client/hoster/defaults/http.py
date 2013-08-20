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

import requests

from .. import host, util

@host
class this:
    name = None
    global_max_check_tasks = 20
    global_max_download_tasks = 20

class IterReader(object):
    def __init__(self, resp):
        self.resp = resp

    def read(self, size):
        from ... import download
        self.iter = self.resp.iter_content(download.config.blocksize)
        self.data = ''
        self.read = self._read
        return self.read(size)

    def _read(self, size):
        try:
            while len(self.data) < size:
                self.data += self.iter.next()
        except StopIteration:
            pass
        data = self.data[:size]
        self.data = self.data[size:]
        return data

    def close(self):
        if self.resp:
            self.resp.raw.release_conn()
            self.resp = None

def handle_download_result(chunk, data):
    if isinstance(data, basestring):
        # assume this is an url
        url = data
        resp = chunk.account.get(url, referer=chunk.referer, chunk=chunk, stream=True)
        return handle_download_result(chunk, resp)
    elif isinstance(data, requests.models.Response):
        # assume we got an requests response object. just parse it and return results
        resp = data
        name, length, size, can_resume = util.http_response(chunk, resp)
        can_resume = chunk.file.can_resume in (None, True) and can_resume and True or False
        chunk.file.set_download_context(can_resume=can_resume)

        update = {}
        if name and chunk.file.name is None:
            update['name'] = name
        if size is not None:
            if chunk.file.size is None:
                update['size'] = size
            elif chunk.file.size != size:
                chunk.log.warning('got response size {} but filesize is {}'.format(size, chunk.file.size))
                update['size'] = size
        if length is not None and chunk.end is not None and length < chunk.end - chunk.pos:
            chunk.log.warning('got response length {} but requested length is {}'.format(length, chunk.end - chunk.pos))
            # TODO: fail here?
        if update:
            chunk.file.set_infos(**update)

        return IterReader(resp), resp.url
    elif hasattr(data, 'read') and callable(data.read):
        # assume r is a stream object
        return data, None
    else:
        raise ValueError(u'unknown return value from on_download: {}'.format(data))

def on_download_next(chunk, data):
    url = data and data or chunk.file.url
    resp = chunk.account.get(url, referer=chunk.referer, chunk=chunk, stream=True)
    name, length, size, can_resume = util.http_response(chunk, resp)
    can_resume = chunk.file.can_resume in (None, True) and can_resume and True or False
    chunk.file.set_download_context(can_resume=can_resume)
    if not chunk.file.can_resume and chunk.pos > 0:
        raise ValueError('chunk is at position {}, but we can not resume'.format(chunk.pos))
    return IterReader(resp)

def on_check(file):
    """possible return values:
    None - file has no postprocessing
    list - core.add_links(result) is called
    tuple - core.add_links(*result) is called
    dict - core.add_links(**result) is called
    when return value is not None and file.name, file.get_any_size() and file.last_error is None the file is deleted after greenlet (file.delete_after_greenlet)"""
    if this.on_check_http:
        resp = file.account.get(file.url, use_cache=False)
        return this.on_check_http(file, resp)
    return util.check_download_url(file, file.url)

on_check_http = None

exports = ['handle_download_result', 'on_download_next']
