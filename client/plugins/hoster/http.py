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
import re
import gevent
from gevent.lock import Semaphore
from contextlib import closing
import base64

from ... import core, hoster, account as account_module, event, logger
from ...scheme import Column, transaction
from bs4 import BeautifulSoup
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

log = logger.get('http')

@event.register('account.domain:changed')
def _(e, account, old):
    if re.match(r'^w:', account.domain):
        account._domain = hoster.wildcard(account.domain)
    elif re.match(r'^r:', account.domain):
        account._domain = hoster.regexp(account.domain)
    else:
        account._domain = re.compile(re.quote(account.domain))


class Account(account_module.Profile, account_module.HttpAccount):
    scheme = Column('api', read_only=False)

    # options
    auth_method = Column('api', read_only=False)
    cookies = Column('api', read_only=False)
    headers = Column('api', read_only=False)

    def __init__(self, **kwargs):
        account_module.Profile.__init__(self, **kwargs)
        account_module.HttpAccount.__init__(self, **kwargs)

        if not self.cookies:
            self.cookies = {}
        if not self.headers:
            self.headers = {}

    def get_login_data(self):
        data = account_module.Profile.get_login_data(self)
        data.update(dict(auth_method=self.auth_method, cookies=self.cookies, headers=self.headers))
        return data

    def match(self, file):
        if self.scheme is not None and self.scheme != file.split_url.scheme:
            return False
        if not account_module.Profile.match(self, file):
            return False
        return True
    
    def _http_request(self, func, *args, **kwargs):
        self._http_request_prepare(kwargs)

        if self.cookies:
            if 'cookies' not in kwargs:
                kwargs['cookies'] = dict()
            kwargs['cookies'].update(self.cookies)

        if self.headers:
            if 'headers' not in kwargs:
                kwargs['headers'] = dict()
            kwargs['headers'].update(self.headers)

        if self.auth_method and (self.username or self.password):
            if self.auth_method == 'basic':
                kwargs['auth'] = HTTPBasicAuth(self.username, self.password)
            elif self.auth_method == 'digest':
                kwargs['auth'] = HTTPDigestAuth(self.username, self.password)
            else:
                self.fatal('unknown auth method: {}'.format(self.auth_method))

        return func(*args, **kwargs)

    def on_initialize(self):
        pass


@hoster.host
class this:
    model = hoster.HttpHoster
    account_model = Account
    name = "http"
    priority = 150
    patterns = [
        hoster.Matcher('https?')
    ]
    config = [
        hoster.cfg('domains', dict(), dict),
        hoster.cfg('send_crawl_domains', False, bool, description='Report domain names that have no plugin')
    ]

_crawl_mime_types = 'text/.*'
_download_mime_types = '.*/.*'
input_lock = Semaphore()

def load_icon(hostname):
    return base64.b64decode(_http_default_icon)

def get_hostname(file):
    return file.split_url.host

def on_check(file):
    # check if we have a multi hoster account for this file
    acc = hoster.get_multihoster_account('check', multi_match, file)
    if acc:
        oldacc = file.account
        try:
            file.log.info('trying multihoster {}, on_check of {}'.format(acc.name, file.url))
            acc.hoster.get_download_context(file, acc)
            return acc.hoster.on_check(file)
        except gevent.GreenletExit:
            raise
        except BaseException as e:
            log.exception(e)
            file.account = oldacc

    # default check code
    with closing(file.account.get(file.url, referer=file.referer, stream=True)) as resp:
        if resp.status_code in (301, 302, 303, 307):
            return [hoster.urljoin(file.url, resp.headers['Location'])]
        hoster.http_response_errors(file, resp)

        content_type = None
        if 'Content-Type' in resp.headers:
            content_type = re.sub('; .*$', '', resp.headers['Content-Type'])

        content_length = None
        if 'Content-Length' in resp.headers:
            content_length = int(resp.headers['Content-Length'])

        content_disposition = None
        if resp.headers.get('Content-Disposition', None) not in (None, 'attachment'):
            content_disposition = resp.headers['Content-Disposition']

        if content_disposition or (content_length and content_length > hoster.MB(2)): # or 'accept-ranges' in resp.headers:
            return _on_check_download(file, resp, content_type, content_length, content_disposition)

        if content_type:
            if re.match(_crawl_mime_types, content_type):
                return _on_check_crawl(file, resp, content_type, content_length, content_disposition)
            elif re.match(_download_mime_types, content_type):
                return _on_check_download(file, resp, content_type, content_length, content_disposition)

        file.delete_after_greenlet()

def _on_check_download(file, resp, content_type, content_length, content_disposition):
    if content_disposition:
        name = hoster.contentdisposition.parse(content_disposition)
    else:
        path = hoster.urlsplit(file.url).path
        name = os.path.basename(path)

    file.set_infos(name, size=content_length)

def _on_check_crawl(file, resp, content_type, content_length, content_disposition):
    # TODO: ask if file sould be parsed or downloaded
    if False:
        return _on_check_download(file, resp, content_type, content_length, content_disposition)

    # send domain to backend
    if this.config.send_crawl_domains:
        domain = file.split_url.host
        log.send('info', 'unknown domain: {}'.format(domain))

    # prase data
    data = resp.text
    data = data.replace('\\/', '/') # lazy method to get also json encoded links
    links = hoster.collect_links(data)

    def _collect(tag, attr):
        for i in soup.select(tag):
            url = i.get(attr)
            if url:
                url = hoster.urljoin(file.url, url)
                if not url in links:
                    links.add(url)

    try:
        soup = BeautifulSoup(data)
        _collect('a', 'href')
        _collect('img', 'src')
        title = soup.select('title')
        if title:
            title = title[0].text
    except UnicodeEncodeError as e:
        file.log.warning('error: {}'.format(e))
        title = file.url

    # filter links
    hoster_links = []
    anonymous_links = []
    if not links:
        return file.no_download_link()
    for url in links:
        try:
            host = hoster.find(url, {'ftp', 'http', 'torrent'})
        except ValueError:
            continue
        link = {'url': url, 'referer': file.url}
        if host:
            link['host'], link['pmatch'] = host
            hoster_links.append(link)
        #elif re.search(r'\.(jpe?g|gif|png|avi|flv|mkv|rar|zip|vob|srt|sub|mp3|mp4|ogg|opus)$', url):
        elif re.search(r'\.(avi|flv|mkv|rar|zip|vob|srt|sub|mp3|mp4|ogg|opus)$', url):
            anonymous_links.append(link)

    if hoster_links:
        core.add_links(hoster_links)
    elif anonymous_links:
        hostname = file.split_url.host
        with input_lock:
            if hostname in this.config.domains:
                add = this.config.domains[hostname]
            else:
                remember, add = file.input_remember_button(['Found #{num} links on #{domain}. Do you want to add them?', dict(num=len(anonymous_links), domain=hostname)])
                if add is None:
                    add = False
                elif remember:
                    with transaction:
                        this.config.domains[hostname] = add
        if add:
            core.add_links(anonymous_links, package_name=title)

    file.delete_after_greenlet()

def get_download_context(file):
    # check if we have a multi hoster account for this file
    acc = hoster.get_multihoster_account('download', multi_match, file)
    if acc:
        # context already set
        return
    else:
        # default http download
        file.set_download_context(
            account=this.get_account('download', file),
            download_func=on_download,
            download_next_func=this.on_download_next)

def on_download(chunk):
    return chunk.file.url


def multi_match(acc, hostname):
    for host in acc.compatible_hosts:
        if host.match(hostname):
            return True
    return False
    
_http_default_icon = """
iVBORw0KGgoAAAANSUhEUgAAAIAAAACACAYAAADDPmHLAAAAGXRFWHRTb2Z0d2FyZQBBZG9iZSBJ
bWFnZVJlYWR5ccllPAAAA2ZpVFh0WE1MOmNvbS5hZG9iZS54bXAAAAAAADw/eHBhY2tldCBiZWdp
bj0i77u/IiBpZD0iVzVNME1wQ2VoaUh6cmVTek5UY3prYzlkIj8+IDx4OnhtcG1ldGEgeG1sbnM6
eD0iYWRvYmU6bnM6bWV0YS8iIHg6eG1wdGs9IkFkb2JlIFhNUCBDb3JlIDUuMy1jMDExIDY2LjE0
NTY2MSwgMjAxMi8wMi8wNi0xNDo1NjoyNyAgICAgICAgIj4gPHJkZjpSREYgeG1sbnM6cmRmPSJo
dHRwOi8vd3d3LnczLm9yZy8xOTk5LzAyLzIyLXJkZi1zeW50YXgtbnMjIj4gPHJkZjpEZXNjcmlw
dGlvbiByZGY6YWJvdXQ9IiIgeG1sbnM6eG1wTU09Imh0dHA6Ly9ucy5hZG9iZS5jb20veGFwLzEu
MC9tbS8iIHhtbG5zOnN0UmVmPSJodHRwOi8vbnMuYWRvYmUuY29tL3hhcC8xLjAvc1R5cGUvUmVz
b3VyY2VSZWYjIiB4bWxuczp4bXA9Imh0dHA6Ly9ucy5hZG9iZS5jb20veGFwLzEuMC8iIHhtcE1N
Ok9yaWdpbmFsRG9jdW1lbnRJRD0ieG1wLmRpZDpDOUFGQzg0RTk4NDhFMjExOTk5OUYzRjU5RTY2
REU0MSIgeG1wTU06RG9jdW1lbnRJRD0ieG1wLmRpZDoxMkJGMThDMDRBOTYxMUUyQUREMzk3MTM0
MjU4QjYzNyIgeG1wTU06SW5zdGFuY2VJRD0ieG1wLmlpZDoxMkJGMThCRjRBOTYxMUUyQUREMzk3
MTM0MjU4QjYzNyIgeG1wOkNyZWF0b3JUb29sPSJBZG9iZSBQaG90b3Nob3AgQ1M2IChXaW5kb3dz
KSI+IDx4bXBNTTpEZXJpdmVkRnJvbSBzdFJlZjppbnN0YW5jZUlEPSJ4bXAuaWlkOkQ4RjUxNDg4
OTI0QUUyMTFCOTM4QUIwMzc5MzZFQTExIiBzdFJlZjpkb2N1bWVudElEPSJ4bXAuZGlkOkM5QUZD
ODRFOTg0OEUyMTE5OTk5RjNGNTlFNjZERTQxIi8+IDwvcmRmOkRlc2NyaXB0aW9uPiA8L3JkZjpS
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
gg==
"""
