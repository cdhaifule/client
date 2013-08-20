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
import time
import ftplib
import gevent
import dateutil.parser

from ... import core, hoster, account, scheme

from gevent.lock import Semaphore

#ftp connection cache

_cache = {}
_cache_lock = Semaphore()
_cache_watchdog = None
CACHE_TIMEOUT = 5

def _set_cache(url, h):
    global _cache_watchdog
    with _cache_lock:
        id = "%s://%s:%s@%s:%s" % (url.scheme, url.username, url.password, url.hostname, url.port)
        if not id in _cache:
            _cache[id] = []
        h._last_used = time.time()
        _cache[id].append(h)
        if _cache_watchdog is None:
            _cache_watchdog = gevent.spawn(_cache_watchdog_service)

def _get_cache(url):
    with _cache_lock:
        id = "%s://%s:%s@%s:%s" % (url.scheme, url.username, url.password, url.hostname, url.port)
        if id in _cache:
            h = _cache[id].pop(0)
            if len(_cache[id]) == 0:
                del _cache[id]
            del h._last_used
            return h

def _cache_watchdog_service():
    global _cache_watchdog
    #TODO: change while True to something like while app_running
    while True:
        with _cache_lock:
            t = time.time()
            for id, h in _cache.iteritems():
                for i in xrange(len(h)):
                    if t - h[i]._last_used > CACHE_TIMEOUT:
                        try:
                            h[i].sendcmd("QUIT")
                        except:
                            pass
                        try:
                            h[i].close()
                        except:
                            pass
                        del _cache[id][i]
                        if len(_cache[id]) == 0:
                            del _cache[id]
                        break
                else:
                    continue
                break
            if not _cache:
                _cache_watchdog = None
                return
        time.sleep(1)

#patch ftplib to handle the PRET command

_pret_cache = {}
def ntransfercmd(self, cmd, *args, **kwargs):
    if not self.host in _pret_cache:
        try:
            self.sendcmd("PRET %s" % cmd)
            _pret_cache[self.host] = True
        except ftplib.error_perm as e:
            if str(e) == '500 Unknown command.':
                _pret_cache[self.host] = False
            else:
                raise
    elif _pret_cache[self.host]:
        self.sendcmd("PRET %s" % cmd)

    return self.ntransfercmd_original(cmd, *args, **kwargs)

class FTP(ftplib.FTP):
    ntransfercmd = ntransfercmd
    ntransfercmd_original = ftplib.FTP.ntransfercmd

class FTP_TLS(ftplib.FTP_TLS):
    ntransfercmd = ntransfercmd
    ntransfercmd_original = ftplib.FTP_TLS.ntransfercmd

#our wrapped ftp class

class MFTP:
    def __init__(self, url=None):
        self.h = None
        self.url = url and hoster.urlsplit(url) or None

    def __del__(self):
        self.close()

    def close(self, cache_connection=True):
        if self.h:
            if cache_connection:
                self.h._last_used = time.time()
                _set_cache(self.url, self.h)
            else:
                try:
                    self.h.sendcmd("QUIT")
                except:
                    pass
                self.h.close()
                del self.h
            self.h = None

    def set_url(self, url):
        self.url = hoster.urlsplit(url)

    def connect(self, url=None):
        self.close()

        if url:
            self.set_url(url)
        if not self.url:
            raise TypeError('no url specified')

        self.h = _get_cache(self.url)
        if self.h:
            return
        elif self.url.scheme == 'ftp':
            self.h = FTP()
        elif self.url.scheme == 'ftps':
            self.h = FTP_TLS()
        else:
            raise ValueError('scheme %s not known' % self.url.scheme)

        try:
            #self.h.debug(1)
            self.h.connect(host=self.url.hostname, port=self.url.port or 21)
            self.h.login(self.url.username or "anonymous", self.url.password or "anonym@ous.com")
        except:
            self.close()
            raise

    def _connect(self):
        if not self.h:
            self.connect()

    def sendcmd(self, *args, **kwargs):
        self._connect()
        return self.h.sendcmd(*args, **kwargs)

    def size(self, *args, **kwargs):
        self._connect()
        return int(self.h.size(*args, **kwargs))

    def cwd(self, *args, **kwargs):
        self._connect()
        return self.h.cwd(*args, **kwargs)

    def retrlines(self, *args, **kwargs):
        self._connect()
        return self.h.retrlines(*args, **kwargs)

    def transfercmd(self, *args, **kwargs):
        self._connect()
        return self.h.transfercmd(*args, **kwargs)

    def getresp(self, *args, **kwargs):
        self._connect()
        return self.h.getresp(*args, **kwargs)

#parsed ftp file from LIST command

class FtpFile:
    def __init__(self, f):
        f = f.split()
        self.mode = f[0]
        self.user = f[2]
        self.group = f[3]
        self.size = int(f[4])
        #this date parsing is buggy on some ftps
        self.date = dateutil.parser.parse(' '.join(f[5:8]))
        self.name = ' '.join(f[8:])
        self.is_dir = self.mode[0] == 'd'
        self.is_link = self.mode[0] == 'l'
        self.is_file = not self.is_dir and not self.is_link

    def __repr__(self):
        return "%s (%s)" % (self.name, self.is_dir and "DIR" or "%s bytes" % self.size)

# main plugins

class Account(account.Profile):
    scheme = scheme.Column('api', read_only=False)

    def __init__(self, **kwargs):
        account.Profile.__init__(self, **kwargs)

    def match(self, file):
        if self.scheme is not None and self.scheme != file.split_url.scheme:
            return False
        if not account.Profile.match(self, file):
            return False
        return True

    def on_initialize(self):
        pass


@hoster.host
class this:
    model = hoster.Hoster
    account_model = Account
    """TODO: add resume/chunk download support"""

    name = "ftp"
    priority = 150

    max_chunks = 1
    can_resume = False

    patterns = [
        hoster.Matcher('ftps?')
    ]

def build_url(ctx):
    if ctx.account.username is None and ctx.account.password is None:
        return ctx.url
    return re.sub(r'(ftps?://)([^@/]+@)?', '\1{}:{}'.format(ctx.account.username or '', ctx.account.password or ''), ctx.url)

def on_check(file):
    url = build_url(file)
    conn = MFTP(url)
    conn.connect()

    try:
        #check if this is a file or folder
        try:
            conn.cwd(conn.url.path)
        except ftplib.error_perm:
            #this seems to be a file
            p = os.path.split(conn.url.path)
            conn.cwd(p[0])

            ls = []
            conn.retrlines("LIST", callback=ls.append)
            for f in ls:
                if re.match('^total \d*$', f):
                    continue

                f = FtpFile(f)
                if f.name != p[1]:
                    continue
                if f.is_link:
                    return
                if f.is_file:
                    file.set_infos(name=f.name, size=f.size)
                    return
                raise ValueError('tried to detect if url is a file but failed: {}'.format(conn.url.path))
            
            #this is a folder
            ls = []
            conn.retrlines("LIST", callback=ls.append)

            results = []
            for f in ls:
                if re.match('^total \d*$', f):
                    continue

                f = FtpFile(f)
                if f.name != p[1]:
                    continue
                if f.is_link:
                    return
                if f.is_file:
                    file.set_infos(name=f.name, size=f.size)
                    return
                raise ValueError('tried to detect if url is a file but failed: {}'.format(conn.url.path))
        
        #this is a folder
        ls = []
        conn.retrlines("LIST", callback=ls.append)

        results = []
        for f in ls:
            if re.match('^total \d*$', f):
                continue

            f = FtpFile(f)
            if f.is_link:
                continue

            if f.is_dir:
                #TODO: crawl that subdirectory? ask user what to do?
                continue

            if not f.is_file:
                #what is this? an alien?
                continue

            results.append({
                "url": "{}/{}".format(str(file.url).rstrip("/"), f.name),
                "name": f.name,
                "size": f.size
            })
        core.add_links(results)
        file.delete_after_check()
    finally:
        conn.close()

def on_download(chunk):
    url = build_url(chunk)
    conn = MFTP(url)
    conn.connect()

    class Response:
        def __init__(self, s):
            self.s = s
            this.closed = False
        
        def __del__(self):
            self.close()
        
        def read(self, size=8192):
            return self.s.recv(size)
        
        def close(self, cache_connection=True):
            self.s.close()
            if not this.closed:
                try:
                    conn.getresp()
                except:
                    cache_connection = False
                if hasattr(self, '_exception') and self._exception:
                    cache_connection = False
                conn.close(cache_connection=cache_connection)
                this.closed = True
        
        def __enter__(self):
            pass
        
        def __exit__(self, *args, **kwargs):
            self.close()

    resp = Response(conn.transfercmd("RETR %s" % conn.url.path))
    return resp
