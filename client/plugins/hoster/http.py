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

    favicon_url = "http://download.am/assets/img/icons/http/128icon.png"

_crawl_mime_types = 'text/.*'
_download_mime_types = '.*/.*'
input_lock = Semaphore()

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

