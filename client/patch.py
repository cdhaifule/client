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
import re
import sys
import time
import stat
import yaml
import gevent
import struct
import binascii
import tarfile
import subprocess
import hashlib
import traceback
import tempfile
import glob
import bz2
import json
import requests
import bsdiff4
import shutil
import dns
import dns.resolver
# imports needed for py2exe
import dns.rdtypes.ANY.TXT
import dns.rdtypes.ANY.CNAME

from gevent import Timeout
from cStringIO import StringIO
from gevent.pool import Group
from gevent.event import Event
from gevent.lock import Semaphore
from gevent.threadpool import ThreadPool
from Crypto.PublicKey import DSA
from requests.exceptions import ConnectionError
from collections import defaultdict

from dulwich import pack
pack.has_mmap = False  # mmap causes never closed .idx files
from dulwich.repo import Repo
from dulwich.client import get_transport_and_path

from . import current, logger, input, settings, reconnect, db, interface, loader, event, core
from .plugintools import Url
from .config import globalconfig
from .scheme import transaction, Table, Column, filter_objects_callback
from .api import proto

config = globalconfig.new('patch')
config.default('branch', 'stable', str, enum=["stable", "unstable", "master"])
config.default('patchtest', False, bool)
config.default('restart', None, str, allow_none=True)
config.default('patch_check_interval', 3600, int)

log = logger.get("patch")

patch_group = Group()
patch_all_lock = Semaphore()

git_threadpool = ThreadPool(10)

test_mode = False


module_initialized = Event()

# update patch interval


@event.register('config:loaded')
def on_config_loaded(e):
    config.patch_check_interval = 3600

# get our platform

platform = sys.platform
if platform == 'linux2':
    import platform
    if platform.machine() == 'x86_64':
        platform = 'linux-amd64'
    else:
        platform = 'linux-i386'
elif platform == "darwin":
    platform = "macos"


# events
@config.register('branch')
def branch_changed(value, old):
    if value != old:
        gevent.spawn_later(5, patch_all)


# signature functions
def check_signature(source, data):
    sizex, sizey = struct.unpack_from('BB', buffer(data, len(data)-2))
    end = len(data) - sizex - sizey-2
    signature = [int(binascii.hexlify(i), 16) for i in struct.unpack_from('{}s{}s'.format(sizex, sizey), buffer(data, end, sizex+sizey))]
    data = buffer(data, 0, end)
    checksum = hashlib.sha384(data).digest()
    if not source.dsa_key.verify(checksum, signature):
        raise RuntimeError("External signature could not be verified.")
    return data


# bindiff patch (outdated?!)

def patch_source_bindiff(source, patch):
    start = "iiQQQQ"
    size = struct.calcsize(start)
    mv = buffer(patch)
    crcsource, crcdest, lencontrol, lendiff, lendest, lenextra = struct.unpack_from(start, mv)
    unpacked = struct.unpack_from("{}s{}s".format(lendiff, lenextra) + "q"*lencontrol, mv, size)
    fc = unpacked[2:]
    control = [tuple(fc[i:i+3]) for i in xrange(0, lencontrol, 3)]
    assert binascii.crc32(source) == crcsource
    dest = bsdiff4.core.patch(source, lendest, control, unpacked[0], unpacked[1])
    assert binascii.crc32(dest) == crcdest
    return dest


# http request functions

def patch_get(url, *args, **options):
    url = "/".join([url.split('#', 1)[0].rstrip('/')]+map(str, args))
    return requests.get(url, **options)

def patch_post(url, *args, **options):
    url = "/".join([url.split('#', 1)[0].rstrip('/')]+map(str, args))
    return requests.post(url, **options)

# dns txt resolve function

def dns_txt_resolve(domain):
    try:
        with Timeout(5):
            query = dns.resolver.query(domain, 'TXT')
    except (Timeout, dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
        return
    except dns.resolver.NoNameservers:
        # try proxied request
        try:
            with Timeout(5):
                resp = requests.get('{}/resolve/txt/{}'.format(settings.patchserver, domain))
            resp.raise_for_status()
            data = resp.json()
            if 'error' in data:
                if data['error'] in {'timeout', 'no_answer', 'nxdomain'}:
                    return
                raise ValueError(u'Proxy resolve error: {}'.format(data['error']))
            assert isinstance(data, list)
            if not data:
                return # raise dns.resolver.NoAnswer
            return data
        except Timeout:
            return
        except:
            raise
    except:
        raise
    else:
        result = list()
        for txt in query:
            if hasattr(txt, 'strings'):
                strings = txt.strings
            else:
                strings = [txt.data[1:]]
            for line in strings:
                result.append(line)
        return result

# the big bad patch process

def rmtree_onerror(func, path, exc_info):
    """
    # http://www.voidspace.org.uk/downloads/pathutils.py
    Error handler for ``shutil.rmtree``.

    If the error is due to an access error (read only file)
    it attempts to add write permission and then retries.

    If the error is for another reason it re-raises the error.

    Usage : ``shutil.rmtree(path, onerror=onerror)``
    """
    if not os.access(path, os.W_OK):
        # Is the error an access error ?
        os.chmod(path, stat.S_IWUSR)
        func(path)
    else:
        raise

def really_clean_repo(path):
    last_e = None
    for i in xrange(10):
        try:
            shutil.rmtree(path, onerror=rmtree_onerror)
            break
        except OSError as e:
            if e.errno == 2:
                break
            last_e = e
        except BaseException as e:
            last_e = e
        gevent.sleep(0.1)
    else:
        raise last_e

class BasicPatchWorker(object):
    def __init__(self, source):
        self.source = source
        self.external = defaultdict(list)

    @classmethod
    def cleanup(cls):
        pass

class GitWorker(BasicPatchWorker):
    def __init__(self, source):
        BasicPatchWorker.__init__(self, source)

    def patch(self):
        return self.fetch()

    @classmethod
    def cleanup(cls):
        pass
        
    def fetch(self, retry=False, old_version=None):
        def on_error(e):
            self.source.log.exception('failed fetching repository')
            with transaction:
                self.source.last_error = 'failed fetching repository: {}'.format(e)
            return False

        broken_file = os.path.join(self.source.basepath, '.broken_repo')
        if os.path.exists(broken_file):
            try:
                really_clean_repo(self.source.basepath)
            except:
                pass

        if old_version is None:
            old_version = self.source.version
        try:
            repo = self.source._open_repo()
            try:
                if repo is None:
                    p = self.source.basepath
                    if not os.path.exists(p):
                        os.makedirs(p)
                    repo = Repo.init_bare(p)

                client, host_path = get_transport_and_path(self.source.url)
                remote_refs = client.fetch(host_path, repo)
                repo["HEAD"] = remote_refs["HEAD"]
            finally:
                if repo:
                    repo.object_store.close()
        except (KeyboardInterrupt, SystemExit, gevent.GreenletExit):
            self.source.unlink()  # it is possible that the clone process is broken when the operation was interrupted
            raise
        except BaseException as e:
            if retry:
                return on_error(e)
            old_exception = sys.exc_info()
            self.source.log.exception('failed fetching repository; deleting repo')
            try:
                really_clean_repo(self.source.basepath)
            except:
                m = re.match('^(.+)-tmp(\d+)$', self.source.basepath)
                if m:
                    basepath = m.group(1)
                    tmp = int(m.group(2)) + 1
                else:
                    basepath = self.source.basepath
                    tmp = 1
                while True:
                    p = u'{}-tmp{}'.format(basepath, tmp)
                    if not os.path.exists(p):
                        break
                    tmp += 1
                try:
                    with open(broken_file, 'w') as f:
                        f.write('delete me')
                except:
                    pass
                self.source.log.unhandled_exception('failed deleting broken repo, trying alternative base path', exc_info=old_exception)
                self.source.basepath = p
            return self.fetch(True, old_version)
        #except BaseException as e:
        #    return on_error(e)
        else:
            self.source.log.debug('fetch complete; fetched ({})'.format(', '.join(remote_refs)))
            new_version = self.source.version
            if old_version == new_version:
                return False
            self.source.log.info('updated branch {} from {} to {}'.format(self.source.get_branch(), old_version, new_version))
            return True

class PatchWorker(BasicPatchWorker):
    def __init__(self, source):
        BasicPatchWorker.__init__(self, source)
        self.new_version = None
        self.patch_data = None
        self.backups = list()

    def patch(self):
        try:
            self.get()
            if self.patch_data:
                self.apply()
                return True
        except ConnectionError as e:
            self.source.log.error('patch error: {}'.format(e))
        return False

    def get(self, retry=1):
        resp = patch_get(self.source.url, "check", self.source.id, self.source.get_branch(), self.source.version)
        resp.raise_for_status()
        if resp.content == "HEAD":
            return

        self.old_version = self.source.version
        if '<html>' in resp.content:
            raise ValueError('invalid response from patch server')
        self.new_version = resp.content
        self.source.log.info('found new version: {}, current version: {}'.format(self.new_version, self.old_version))

        resp = patch_get(self.source.url, "download", "patch", self.source.id, self.source.version, self.new_version, allow_redirects=False)
        resp.raise_for_status()
        if resp.status_code == 200 and resp.headers["content-type"] == "application/force-download":
            data = resp.content
        else:
            if resp.status_code != 201:
                self.source.log.debug("patch not ready (retry {}): {}".format(retry, resp.content))
                #if retry <= 4: # wait at least 10 seconds for the patch
                #    gevent.sleep(retry)
                #    return self.get(retry + 1)
                return
            resp = requests.get(resp.headers["Location"])
            resp.raise_for_status()
            data = resp.content

        self.patch_data = check_signature(self.source, data)
        self.patch_data = bz2.decompress(self.patch_data)
    
    # apply functions
    
    def apply(self):
        buf = StringIO()
        buf.write(self.patch_data)
        buf.seek(0)

        tar = tarfile.open(None, "r", buf)
        for info in tar.getmembers():
            data = tar.extractfile(info)
            if data is None:
                continue
            data = data.read()
            if info.name == ".delete":
                self.apply_delete(data)
            elif info.name.startswith("patch/"):
                self.apply_patch(data, info.name)
            else:
                self.apply_new(data, info.name)

        self.source.log.info("updated from {} to {}".format(self.old_version, self.new_version[:7]))
        with transaction:
            self.source.version = self.new_version[:7]

    def add_external_new(self, file, data):
        try:
            with open(file+'.new', 'wb') as f:
                f.write(data)
        except (IOError, OSError):
            self.source.log.exception('error writing fallback file for {}'.format(file))
            # FATAL
            return True
        self.external['replace'].append(file)

    def add_external_delete(self, file):
        self.external['delete'].append(file)

    def create_backup(self, file, data):
        # create backup
        try:
            with open(file+".old", "wb") as f:
                f.write(data)
        except (OSError, IOError):
            self.source.log.warning("error creating backup for {}".format(file))
        self.backups.append(file+'.old')

    def apply_delete(self, data):
        # files to be deleted
        for name in data.splitlines():
            path = self.source.relpath(name.strip())
            if path is None:
                continue
            files = [path] + glob.glob(path+"?")
            for file in files:
                try:
                    if not os.path.exists(file):
                        continue

                    self.source.log.info("deleting file {}".format(file))

                    # delete .py? files
                    if file.endswith('.py'):
                        for f in glob.glob(file+'?'):
                            try:
                                os.unlink(f)
                            except:
                                self.add_external_delete(f)

                    # create backup
                    try:
                        with open(file, 'rb') as f:
                            old_data = f.read()
                        self.create_backup(file, old_data)
                    except:
                        traceback.print_exc()
                        self.source.log.warning("error in backup creation of {} will be ignored.".format(file))

                    # delete file
                    os.unlink(file)
                except (IOError, OSError) as e:
                    if e.errno == 2:
                        self.source.log.warning("file to delete not found: {}".format(name))
                    else:
                        self.source.log.critical("error deleting file {}".format(name))
                        self.add_external_delete(file)

    def apply_new(self, data, name):
        file = self.source.relpath(name)
        if file is None:
            return False

        # create directories
        path = os.path.dirname(file)
        if not os.path.exists(path):
            os.makedirs(path)

        # create backup
        try:
            with open(file, 'rb') as f:
                old_data = f.read()
        except (IOError, OSError):
            pass
        else:
            self.create_backup(file, old_data)

        # create new file
        try:
            self.source.log.info("creating file {}".format(file))
            with open(file, "wb") as f:
                f.write(data)
        except (IOError, OSError):
            self.source.log.exception("error writing file {}".format(file))
            self.add_external_new(file, data)

    def apply_original(self, file):
        """replaces a corrupt file with the original
        """
        if file.startswith("patch/"):
            file = file[6:]
        resp = patch_get(self.source.url, 'getfile', self.source.id, self.new_version, params=dict(name=file))
        resp.raise_for_status()
        data = check_signature(self.source, resp.content)
        return self.apply_new(data, file)

    def apply_patch(self, patch_data, name):
        patch_file, format = os.path.splitext(name)
        file = self.source.relpath(patch_file)
        if file is None:
            return

        if format == ".bindiff":
            patch_func = patch_source_bindiff
        elif format == ".bsdiff4":
            patch_func = bsdiff4.patch
        else:
            self.source.log.error("unknown patch format {} for file {}".format(format, name))
            return True

        try:
            with open(file, "rb") as f:
                old_data = f.read()
        except (OSError, IOError) as e:
            self.source.log.exception("error opening source file {}".format(file))
            return self.apply_original(patch_file)

        # create backup
        self.create_backup(file, old_data)

        try:
            new_data = patch_func(old_data, patch_data)
        except AssertionError:
            self.source.log.error("crc check of source or destination file {} failed. downloading original".format(name))
            return self.apply_original(patch_file)

        for i in xrange(1, 3):
            try:
                self.source.log.info("apply patch (retry {}): {}".format(i, file))
                with open(file, 'wb') as f:
                    f.write(new_data)
                break
            except (IOError, OSError) as e:
                self.source.log.warning("error replacing {}: {}".format(file, e))
                gevent.sleep(0.1)
        else:
            self.add_external_new(file, new_data)

worker_classes = [GitWorker, PatchWorker]

# patch tasks

def patch_one(patches, source, timeout=180):
    with source.lock:
        if source.get_config_url() is not None:
            source.get_config_url().update()
            if source._table_deleted:
                return
        try:
            p = source.get_worker()
            with Timeout(timeout):
                if p.patch():
                    patches.append(p)
        except Timeout:
            source.log.error('patch function timed out')
        except requests.ConnectionError as e:
            source.log.error('patch error: {}'.format(e))
        except (KeyboardInterrupt, SystemExit, gevent.GreenletExit):
            raise
        except BaseException as e:
            with transaction:
                source.last_error = str(e)
            source.log.exception('patch exception')

def patch_all(timeout=180, external_loaded=True, source_complete_callback=None):
    with patch_all_lock:
        # check config urls
        """log.debug('checking config urls')
        todo = list()
        for source in sources.values():
            config_url = source.get_config_url()
            if config_url is not None and config_url not in todo:
                todo.append(config_url)

        group = Group()
        for config_url in todo:
            g = group.spawn(config_url.update)
            patch_group.add(g)
        group.join()"""

        # check for updates
        log.debug('updating repos')
        group = Group()
        patches = list()
        try:
            for source in sources.values():
                if source.enabled:
                    def _patch(patches, source, timeout):
                        try:
                            patch_one(patches, source, timeout)
                        finally:
                            if source_complete_callback is not None:
                                source_complete_callback(source)
                    g = group.spawn(_patch, patches, source, timeout)
                    patch_group.add(g)
            group.join()
        finally:
            for cls in worker_classes:
                cls.cleanup()
        finalize_patches(patches, external_loaded=external_loaded)

def patch_loop():
    while True:
        gevent.sleep(config.patch_check_interval)
        try:
            if not reconnect.manager.reconnecting:
                patch_all()
        except (KeyboardInterrupt, SystemExit, gevent.GreenletExit, gevent.GreenletExit):
            raise
        except:
            log.unhandled_exception('patch_loop')

pending_external = dict(replace=list(), delete=list(), deltree=list())

def finalize_patches(patches, external_loaded=True):
    """returns True when app needs a restart
    """
    if not patches:
        return

    for p in patches:
        pending_external['replace'] += p.external['replace']
        pending_external['delete'] += p.external['delete']
        pending_external['deltree'] += p.external['deltree']

    if pending_external['replace'] or pending_external['delete'] or pending_external['deltree'] or external_loaded or any(isinstance(p.source, CoreSource) for p in patches):
        log.info('applied patches need a restart')
        if external_loaded:
            # app is running. ask the user if we should restart
            restart_app()
        else:
            # currently in bootstrap. instant restart
            execute_restart()
    else:
        log.info('patches applied on the fly')


# restart functions

def restart_app():
    from . import download
    if download.strategy.has('patch'):
        return

    while True:
        if not core.global_status.files_working:
            result = "now"
            break
        if config.restart is not None and config.restart != "never":
            result = config.restart
            break

        elements = list()
        elements += [input.Text('Some new updates were installed. You have to restart Download.am to apply them.')]
        elements += [input.Text('Restart now?')]
        elements.append(input.Input('remember', 'checkbox', default=False, label='Remember decision?'))
        elements += [input.Choice('answer', choices=[
            {"value": 'now', "content": "Yes"},
            {"value": 'later', "content": "Ask me later"},
            {"value": 'after_download', "content": "When downloads are complete"},
            {"value": 'never', "content": "No"}
        ])]
        try:
            r = input.get(elements, type='patch', timeout=120)
            result = r.get('answer', 'later')
            if r.get('remember', False):
                config.restart = result
        except input.InputTimeout:
            log.warning('input timed out')
            result = 'later'
        except input.InputError:
            log.exception('input was aborted')
            result = 'later'
        except BaseException as e:
            log.exception('input was aborted due to exception: {}'.format(e))
            result = 'later'
        break
    if result == 'never':
        log.info('will not restart to apply the update')
    elif result == 'later':
        gevent.spawn_later(600, restart_app)
    elif result == 'now':
        gevent.sleep(0.5)
        execute_restart()
    elif result == 'after_download':
        if not download.strategy.has('patch'):
            # downloads need time to move to complete folder so sleep a short time
            download.strategy.off('patch', gevent.spawn_later, 10, execute_restart)
    else:
        raise NotImplementedError()

def build_argv(argv):
    if module_initialized.is_set():
        if '--no-browser' not in argv:
            argv.append('--no-browser')
        if '--disable-splash' not in argv:
            argv.append('--disable-splash')
    if platform == 'win32':
        if core.config.shutdown:
            if '--shutdown' not in argv:
                argv.append('--shutdown')
        elif '--shutdown' in argv:
            argv.remove('--shutdown')

def execute_restart():
    replace = pending_external and pending_external['replace'] or list()
    delete = pending_external and pending_external['delete'] or list()
    deltree = pending_external and pending_external['deltree'] or list()
    if platform == "macos" or platform.startswith("linux"):
        cmd = sys.executable
        argv = sys.argv[:]
        build_argv(argv)
    else:
        argv = sys.argv[1:]
        build_argv(argv)
        if sys.__stdout__.isatty():
            if replace or delete or deltree:
                cmd = '"' + sys.executable + '"'
                if argv:
                    cmd += ' "' + '" "'.join(argv) + '"'
                argv = list()
            else:
                cmd = sys.executable
        else:
            cmd = 'cmd /c start "" '
            cmd += '"' + sys.executable + '"'
            if argv:
                cmd += ' "' + '" "'.join(argv) + '"'
                argv = list()
    if replace or delete or deltree:
        if platform == "win32":
            return _external_rename_bat(replace, delete, deltree, cmd, argv)
        else:
            return _external_rename_sh(replace, delete, deltree, cmd, argv)
    else:
        replace_app(cmd, *argv)

def _external_rename_bat(replace, delete, deltree, cmd, argv):
    code = list()
    code.append('@echo off')
    code.append('ping -n 3 127.0.0.1 >NUL') # dirty method to sleep 2 seconds
    for file in replace:
        code.append('move /y "{}" "{}"'.format(file+'.new', file))
    for file in delete:
        code.append('del /Q "{}"'.format(file))
    for file in deltree:
        code.append('del /S/Q "{}"'.format(file))

    code.append(cmd)

    code.append('del /Q "%0"')
    print '\r\n'.join(code)

    tmp = tempfile.NamedTemporaryFile(suffix=".bat", delete=False)
    tmp.write('\r\n'.join(code))
    tmp.close()

    replace_app(tmp.name)

def _external_rename_sh(replace, delete, deltree, cmd, argv):
    code = list()
    code.append('sleep 2')
    for file in replace:
        file = file.replace("'", "\\'")
        code.append("mv '{}' '{}'".format(file+'.new', file))
    for file in delete:
        file = file.replace("'", "\\'")
        code.append("rm -f '{}'".format(file))
    for file in deltree:
        file = file.replace("'", "\\'")
        code.append("rm -rf '{}'".format(file))

    code.append('rm "$0"')

    if platform == "macos":
        code.append('open {}'.format(settings.app_dir))
    else:
        # this code is only tested with console version
        code.append("'{}' '{}'".format(cmd, "' '".join(argv)))

    print '\n'.join(code)

    with tempfile.NamedTemporaryFile(suffix=".sh", delete=False) as tmp:
        tmp.write('\n'.join(code))
    
    replace_app("/bin/sh", tmp.name)

def replace_app(cmd, *args):
    if platform == 'macos':
        from PyObjCTools import AppHelper
        args = list(args)
        AppHelper.stopEventLoop()
        aboot = args[0].replace('loader_darwin', '__boot__')
        if os.path.exists(aboot):
            args[0] = aboot
    elif platform == 'linux':
        os.chdir(settings.app_dir)
    print "REPLACE APP:", cmd, args
    try:
        if platform != "macos":
            loader.terminate()
    finally:
        if hasattr(sys, 'exitfunc'):
            sys.exitfunc()
        #os.chdir(settings.app_dir)
        if platform == 'win32' and not sys.__stdout__.isatty():
            subprocess.Popen(cmd, creationflags=0x08000000)
        else:
            os.execl(cmd, cmd, *args)
        sys.exit(0)

# file iterator classes

class HddIterator(object):
    def __init__(self, path, walk):
        self.path = path
        self.walk = walk

    def __iter__(self):
        if os.path.isfile(self.path):
            path, name = os.path.split(self.path)
            yield HddFile(path, name)
        else:
            for name in os.listdir(self.path):
                path = os.path.join(self.path, name)
                if self.walk and os.path.isdir(path):
                    for file in HddIterator(path, True):
                        yield file
                elif os.path.isfile(path):
                    yield HddFile(self.path, name)

class HddFile(object):
    def __init__(self, path, name):
        self.path = path
        self.name = name

    def get_contents(self):
        gevent.sleep(0)
        with open(os.path.join(self.path, self.name), 'rb') as f:
            return f.read()

class GitFile(object):
    def __init__(self, path, contents):
        self.path, self.name = os.path.split(path)
        self.contents = contents

    def get_contents(self):
        return self.contents


# source classes

sources = dict()
config_urls = dict()

class ConfigUrl(object):
    def __init__(self, url):
        self.url = url
        self.lock = Semaphore()
        self.last_update = None

    @property
    def log(self):
        if not hasattr(self, '_log'):
            self._log = logger.get('patch.config_url.{}'.format(self.url))
        return self._log

    def update(self):
        locked = self.lock.locked()
        with self.lock:
            if locked:
                return
            if self.last_update is not None and time.time() - self.last_update < 60:
                return
            try:
                self._update()
            except requests.ConnectionError as e:
                self.log.error('update error: {}'.format(e))
            finally:
                self.last_update = time.time()

    def _update(self):
        data = dict()

        u = Url(self.url)
        try:
            query = dns_txt_resolve(u.host)
        except:
            log.unhandled_exception('error resolving txt records for {}'.format(u.host))
        else:
            if query is not None:
                for line in query:
                    if line.startswith('repo-domain='):
                        self.url = u'http://{}/'.format(line[12:])
                        return self._update()
                    elif re.match(r'^\w+: \w+://', line):
                        key, value = line.split(': ', 1)
                        data[key] = value
            assert len(data.keys()) > 0

        if not data and 'dlam-config.yaml' in self.url:
            with Timeout(10):
                resp = requests.get(self.url)
            resp.raise_for_status()
            buf = StringIO()
            buf.write(resp.content)
            buf.seek(0)
            data = yaml.load(buf)
            if not isinstance(data, dict):
                self.log.send('error', 'config url returned invalid data: {} (type {})'.format(resp.content, type(data)))
            assert isinstance(data, dict), 'config url returned invalid data: {}'.format(data)
            assert len(data.keys()) > 0
        
        if not data.keys():
            return
        #assert len(data.keys()) > 0

        found_sources = list()
        group = Group()

        def _add_source(url):
            try:
                source = add_source(url, self.url)
            except:
                self.log.exception('error adding new repo {}'.format(url))
            else:
                found_sources.append(source)

        for name, url in data.iteritems():
            try:
                Url(url)
            except:
                self.log.warning('invalid patch source entry: {}'.format(url))
            try:
                source = sources[name]
            except KeyError:
                self.log.info('adding new repo {}'.format(url))
                group.spawn(_add_source, url)
            else:
                found_sources.append(source)
                if source.url != url:
                    source.log.info('changing url to {}'.format(url))
                    with transaction:
                        source.url = url
                    source.unlink()
        group.join()

        for source in sources.values():
            if source.config_url == self.url and source not in found_sources:
                source.log.info('erasing repo')
                source.delete(True)


class BasicSource(Table):
    _table_name = "patch_source"
    _table_collection = sources

    id = Column()
    enabled = Column(always_use_getter=True)
    last_error = Column(always_use_getter=True)

    def __init__(self, enabled=True, config_url=None, **kwargs):
        self.enabled = enabled
        self.config_url = config_url

        for k, v in kwargs.iteritems():
            setattr(self, k, v)

        self.last_error = None
        self.basepath = os.path.join(settings.external_plugins, self.id)

        self.lock = Semaphore()
        self.get_config_url()

    @property
    def log(self):
        if not hasattr(self, '_log'):
            self._log = logger.get('patch.source.{}'.format(self.id))
        return self._log

    def get_config_url(self):
        if self.config_url is None:
            return None
        if self.config_url not in config_urls:
            config_urls[self.config_url] = ConfigUrl(self.config_url)
        return config_urls[self.config_url]

    def on_get_enabled(self, value):
        if os.path.exists(os.path.join(self.basepath, '.git')):
            return False
        return value

    def on_get_last_error(self, value):
        if value is not None:
            return value
        if os.path.exists(os.path.join(self.basepath, '.git')):
            return 'developement mode'
        return None

    def get_branch(self):
        branches = self.branches
        for branch in (('{}-{}'.format(self.id, config.branch), config.branch), (config.branch, config.branch), ('{}-master'.format(self.id), 'master'), ('master', 'master')):
            if branch[0] in branches:
                return branch[1]
        return "master"
        #raise ValueError("repo has no master branch")

    def iter_files(self, path=None):
        raise NotImplementedError()

    def get_repo_url(self):
        raise NotImplementedError()

    def get_worker(self):
        raise NotImplementedError()

    def check(self):
        raise NotImplementedError()

    def send_error(self, id, name, type, message, content):
        raise NotImplementedError()

    def unlink(self):
        raise NotImplementedError()

    def delete(self, erase):
        raise NotImplementedError()

class PublicSource(object):
    pass


class BasicPatchSource(BasicSource):
    def __init__(self, branches=None, version=None, **kwargs):
        BasicSource.__init__(self, **kwargs)
        self.branches = branches if branches else dict()
        self.version = version or '0'*7
        self.sent_errors = set()

    @property
    def dsa_key(self):
        if not hasattr(self, '_dsa_key'):
            self._dsa_key = DSA.construct(self.sig)
        return self._dsa_key

    def get_repo_url(self):
        return '{}#{}'.format(self.url, self.id)

    def relpath(self, path):
        if path.startswith("patch/"):
            path = path[6:]
        return os.path.join(self.basepath, path)

    def get_worker(self):
        return PatchWorker(self)

    def check(self):
        with self.lock:
            if not self.enabled:
                return
            
            with transaction:
                self.last_error = None

            try:
                resp = patch_get(self.url, 'expose', self.id)
                resp.raise_for_status()
                data = resp.json()

                data['sig'] = [long(n) for n in data['sig']]
                if data["sig"] != self.sig:
                    self.log.error("signature changed! this could be bad")
                    result = self.on_invalid_sig()

                    if result == "disable":
                        with transaction:
                            self.enabled = False
                        raise ValueError('invalid signature')

                    with transaction:
                        self.sig = data['sig']

                with transaction:
                    self.contact = data['contact']
                    self.branches = data['repo']
            except (KeyboardInterrupt, SystemExit, gevent.GreenletExit):
                raise
            except BaseException as e:
                with transaction:
                    self.last_error = str(e)
                raise

    def on_invalid_sig(self):
        elements = list()
        elements += [input.Text('Signature of source {} changed.'.format(self.url))]
        elements += [input.Text('This could be an attempt to attack your computer. Please contact the author {}'.format(self.contact))]
        elements += [input.Choice('answer', choices=[
            {"value": 'ignore', "content": "Ignore and update signature?"},
            {"value": 'disable', "content": "Disable source? (very recommended)"},
        ])]
        try:
            r = input.get(elements, type='patch', timeout=120)
            return r['answer']
        except input.InputTimeout:
            self.log.warning('input timed out')
            return 'disable'
        except input.InputError:
            self.log.error('input was aborted')
            return 'disable'

    def send_error(self, id, name, type, message, content):
        if self.version == 'DEV':
            return
        with self.lock:
            if id in self.sent_errors:
                return
            self.sent_errors.add(id)

            data = dict(
                id=id,
                version='{}:{}@{}'.format(platform, self.get_branch(), str(self.version)[:7]),
                name=name,
                type=type,
                message=message,
                content=content)
            for i in xrange(10):
                try:
                    resp = patch_post(self.url, 'log_error', data=data)
                    resp.raise_for_status()
                    self.log.info('sent error {} to backend'.format(id))
                    return
                except (KeyboardInterrupt, SystemExit, gevent.GreenletExit):
                    raise
                except requests.ConnectionError as e:
                    self.log.error('error sending error: {}'.format(e))
                except BaseException:
                    self.log.exception('error while sending error {} to backend'.format(id))
                    gevent.sleep(10)
            self.log.error('giving up sending error {} to backend'.format(id))


class CoreSource(BasicPatchSource):
    version = Column(always_use_getter=True)

    def __init__(self, **kwargs):
        BasicPatchSource.__init__(self, **kwargs)
        self.basepath = settings.app_dir

    def on_get_version(self, value):
        return current.current[:7]

    def on_set_version(self, value):
        # just ignore this call. version is set over current.py
        return None

    def on_get_enabled(self, value):
        if current.current == 'DEV':
            return False
        return BasicPatchSource.on_get_enabled(self, value)

    def get_branch(self):
        return config.branch

    def relpath(self, p):
        if self.id == "win32" and sys.frozen:
            if "download.am/" not in p:
                log.error("path {} in patchfile is not part of distribution.".format(p))
                return None
            return os.path.join(settings.app_dir, p.split("download.am/", 1)[1].replace("/", os.sep))
        elif self.id == "macos":
            if "download.am.app/" not in p:
                log.error("path {} in patchfile is not part of distribution.".format(p))
                return None
            after = p.rsplit("download.am.app/", 1)[1]
            return os.path.join(settings.app_dir, after)
        elif config["patchtest"]:
            return os.path.join(settings.app_dir, p.split("download.am/", 1)[1].replace("/", os.sep))
        else:
            # xxx should use git directly probably when using undistributed environment
            return None
        
    def delete(self, erase):
        raise RuntimeError('not allowed to delete core source')

class PatchSource(BasicPatchSource, PublicSource):
    id = Column(('db', 'api'))
    url = Column(('db', 'api'))
    config_url = Column(('db', 'api'))
    sig = Column('db')
    contact = Column(('db', 'api'))

    branches = Column(('db', 'api'))
    version = Column(('db', 'api'))
    enabled = Column(('db', 'api'), read_only=False, always_use_getter=True)

    last_error = Column(('db', 'api'))

    def __init__(self, **kwargs):
        BasicPatchSource.__init__(self, **kwargs)

    def on_get_version(self, value):
        if not os.path.exists(self.basepath):
            return '0'*7
        return value

    def iter_files(self, path=None, walk=False):
        path = self.basepath if path is None else os.path.join(self.basepath, path)
        return HddIterator(path, walk)

    def unlink(self):
        try:
            really_clean_repo(self.basepath)
        except:
            pending_external['deltree'].append(self.basepath)

    def delete(self, erase):
        if erase:
            self.unlink()
        with transaction:
            self.table_delete()
        self.log.info('deleted')
        gevent.spawn_later(1, restart_app)

    def send_error(self, id, name, type, message, content):
        return core_source.send_error(self, id, name, type, message, content)


class GitSource(BasicSource, PublicSource):
    id = Column(('db', 'api'))
    url = Column(('db', 'api'))
    config_url = Column(('db', 'api'))

    branches = Column('api', always_use_getter=True)
    version = Column('api', always_use_getter=True)
    enabled = Column(('db', 'api'), read_only=False, always_use_getter=True)

    last_error = Column(('db', 'api'))

    def __init__(self, **kwargs):
        BasicSource.__init__(self, **kwargs)

    def _open_repo(self):
        if os.path.exists(os.path.join(self.basepath, '.git')):
            return None
        try:
            return Repo(self.basepath)
        except:
            return None

    def on_get_branches(self, value):
        repo = self._open_repo()
        if repo is None:
            return list()
        try:
            return list(i.rsplit("/", 1)[1] for i in repo.get_refs() if i.startswith("refs/heads/"))
        finally:
            repo.object_store.close()

    def on_get_version(self, value):
        repo = self._open_repo()
        if repo is None:
            return '0'*7
        try:
            x = repo.get_refs()["refs/heads/" + self.get_branch()]
            return x[:7]
        except KeyError:
            return '0'*7
        finally:
            repo.object_store.close()
    
    def get_worker(self):
        return GitWorker(self)

    def get_repo_url(self):
        return self.url

    def check(self):
        pass

    send_error = None

    def iter_files(self, path='', walk=False):
        if os.path.exists(os.path.join(self.basepath, '.git')):
            path = self.basepath if path == '' else os.path.join(self.basepath, path)
            return HddIterator(path, walk)
        else:
            repo = self._open_repo()
            if repo is None:
                return list()

            try:
                data = list()
                startswith = os.path.join(self.basepath, path)
                for entry in repo.object_store.iter_tree_contents(repo["refs/heads/"+self.get_branch()].tree):
                    path = entry.in_path(repo.path).path
                    if platform == 'win32':
                        path = path.replace('/', os.sep)
                    if not path.startswith(startswith):
                        continue
                    gevent.sleep(0)
                    data.append(GitFile(path, repo[entry.sha].as_raw_string()))
                return data
            finally:
                repo.object_store.close()

    def unlink(self):
        try:
            really_clean_repo(self.basepath)
        except:
            pending_external['deltree'].append(self.basepath)

    def delete(self, erase):
        with self.lock:
            if erase:
                self.unlink()
            with transaction:
                self.table_delete()
            self.log.info('deleted')
            gevent.spawn_later(1, restart_app)


############### get source file iterators

def get_file_iterator(source_name, path=None, walk=False):
    kwargs = dict(walk=walk)
    if path is not None:
        kwargs['path'] = path

    try:
        source = sources[source_name]
    except KeyError:
        kwargs['path'] = os.path.join(settings.external_plugins, source_name, path)
        files = HddIterator(**kwargs)
    else:
        files = source.iter_files(**kwargs)

    return files


############### add sources

def add_config_source(url, config_url=None):
    if config_url is not None:
        raise ValueError('config url not allowed on config sources')
    if url not in config_urls:
        config_urls[url] = ConfigUrl(url)
    config_urls[url].update()
    return config_urls[url]

def add_git_source(url, config_url=None):
    u = Url(url)
    try:
        id = os.path.split(u.path)[1]
        id = os.path.splitext(id)[0]
    except:
        raise ValueError('not a git url')
    if id in sources:
        raise ValueError('source with name {} already exists'.format(id))
    with transaction:
        return GitSource(id=id, url=url, config_url=config_url)

def add_patch_source(url, config_url=None):
    if not url.startswith('http'):
        url = 'http://{}'.format(url)
    if '#' not in url and not url.endswith('/'):
        url = '{}/'.format(url)

    u = Url(url)

    if not u.fragment:
        # try to add all repos from this server
        resp = patch_get(url.rstrip('/'), 'expose')
        resp.raise_for_status()
        try:
            data = resp.json()
            for id in data['repos']:
                if id not in sources and id not in ('win32', 'macos'):
                    try:
                        add_patch_source(url+'#'+id, False)
                    except BaseException as e:
                        log.error('failed adding repo {}: {}'.format(id, e))
            return
        except:
            raise ValueError('invalid repo url')
    if u.fragment in sources:
        raise ValueError('source with name {} already exists'.format(u.fragment))

    id = u.fragment

    try:
        resp = patch_get(url, 'expose', id)
        resp.raise_for_status()
        data = resp.json()
    except (ConnectionError, requests.HTTPError) as e:
        log.info("error checking for repo {} at {}: {}".format(id, url, e))
        return

    id = data['name']
    if id in sources:
        raise ValueError('source with name {} already exists'.format(id))

    data = json.loads(resp.content)
    with transaction:
        return PatchSource(id=id, url=url, config_url=config_url, sig=data['sig'], contact=data['contact'], branches=data['repo'])

source_types = dict(
    git=add_git_source,
    patch=add_patch_source,
    config=add_config_source)

def identify_source(url):
    # repair url
    if '://' not in url:
        url = 'http://'+url
        if not Url(url).path:
            url = url+'/'

    u = Url(url)
    u.host = u.host.lower()
    url = u.to_string()

    if url.endswith('.git'):
        return 'git', url

    if 'dlam-config.yaml' in url:
        return 'config', url

    u = Url(url)
    try:
        query = dns_txt_resolve(u.host)
    except (Timeout, dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
        pass
    except:
        log.unhandled_exception('error resolving txt records for {}'.format(u.host))
    else:
        if query is not None:
            for line in query:
                if line.startswith('repo-domain='):
                    return identify_source(line[12:])
                elif re.match(r'^\w+: \w+://', line):
                    return 'config', url

    if not u.host.startswith('www.'):
        u.host = 'www.{}'.format(u.host)
        return identify_source(u.to_string())

    try:
        with Timeout(10):
            u = url.rstrip('/')+'/dlam-config.yaml'
            resp = requests.get(u)
            resp.raise_for_status()
            return 'config', u
    except:
        traceback.print_exc()

    raise ValueError('could not identify source')

def add_source(url, config_url=None, type=None):
    if type is None:
        try:
            with Timeout(30):
                type, url = identify_source(url)
        except Timeout:
            log.warning('add source check timed out')
            return
        except BaseException:
            raise
    return source_types[type](url, config_url)

# startup/shutdown

patch_loop_greenlet = None
core_source = None

emergency_patch = False

def init():
    global patch_loop_greenlet
    global core_source

    # add core source
    sig = [
        14493609762890313342166277786717882067186706504725349899906780741747713356290787356528733464152980047783620946593111196306463577744063955815402148552860145629259653950818107505393643383587083768290613402372295707034951885912924020308782786221888333312179957359121890467597304281160325135791414295786807436357,
        1836340799499544967344676626569366761238237327637553699677615341837866857178638560803752775147141401436473176143062386392930849127511639810150938435062071285028855634164277748937448362731305104091415548874264676030905340846245037152836818535938439214826659048244377315288514582697466079356264083762738266643,
        89884656743115795873895609296394864029741047392531316591432509289601210992615631812974174607675153482641606235553368183778569185786977952044726620763937252233940116059625337686768538445873713070762889839480360220508177637118657209098549890835520224254015051271431737736621385544038152276933973262030194906397,
        1224239220300762038953555488069442663256999688439
    ]
    with transaction:
        core_source = CoreSource(id=platform, url=settings.patchserver, sig=sig, contact='support@download.am')

    # load sources
    try:
        with transaction, db.Cursor() as c:
            aa = c.execute("SELECT * FROM patch_source")
            for a in aa.fetchall():
                try:
                    id = json.loads(a['id'])
                    data = json.loads(a['data'])
                    # update old repo urls
                    if 'url' in data:
                        if data['url'].startswith('http://patch.download.am'):
                            data['url'] = data['url'].replace('http://patch.download.am', 'http://repo.download.am')
                        if data['url'].startswith('http://community.download.am'):
                            data['url'] = data['url'].replace('http://community.download.am', 'http://www.download.am')
                        u = Url(data['url'])
                        u.host = u.host.lower()
                        data['url'] = u.to_string()
                    if 'config_url' in data and data['config_url']:
                        u = Url(data['config_url'])
                        u.host = u.host.lower()
                        data['config_url'] = u.to_string()

                    if 'url' in data and data['url'].endswith('.git'):
                        source = GitSource(id=id, **data)
                    else:
                        source = PatchSource(id=id, **data)
                    if source.enabled:
                        patch_group.spawn(source.check)
                except TypeError:
                    log.critical(u"broken row: {}".format(a))
                    traceback.print_exc()
    except:
        if emergency_patch:
            traceback.print_exc()
        else:
            raise

    # delete useless repos
    if not emergency_patch and not test_mode:
        for extern in os.listdir(settings.external_plugins):
            path = os.path.join(settings.external_plugins, extern)
            if not os.path.isdir(path):
                continue
            if os.path.exists(os.path.join(path, '.git')):
                continue
            if extern not in sources or not sources[extern].enabled or os.path.exists(os.path.join(path, '.broken_repo')):
                log.info(u'deleting useless external repo {}'.format(path))
                try:
                    really_clean_repo(path)
                except:
                    pass

    if not emergency_patch and not test_mode:
        if os.path.exists(os.path.join(settings.bin_dir, 'extern')):
            for name in os.listdir(os.path.join(settings.bin_dir, 'extern')):
                path = os.path.join(settings.bin_dir, 'extern', name)
                if not os.path.isdir(path):
                    continue
                if not os.path.exists(os.path.join(path, 'HEAD')):
                    continue
                target = os.path.join(settings.external_plugins, name)
                if os.path.exists(target):
                    continue
                yield u'copying preloaded default repo {}'.format(name)
                try:
                    shutil.copytree(path, target)
                except:
                    try:
                        really_clean_repo(path)
                    except:
                        pass
                    log.unhandled_exception('error copying preloaded default repo {}'.format(name))

        default_sources = dict(
            downloadam='download.am'
        )
        for id, url in default_sources.iteritems():
            if id not in sources and url not in config_urls:
                yield u'adding default repo {}'.format(id)
                try:
                    source = add_source(url)
                    if source is None:
                        continue
                except:
                    traceback.print_exc()
                else:
                    if isinstance(source, BasicSource) and source.enabled:
                        patch_group.spawn(source.check)

    # check and apply updates
    from gevent.queue import JoinableQueue
    y = JoinableQueue()
    complete = list()

    def source_complete_callback(source):
        complete.append(source)
        if len(complete) == len(sources):
            y.put('updating {} / {}'.format(len(complete), len(sources)))

    gevent.spawn(patch_all, 30, False, source_complete_callback=source_complete_callback)
    gevent.sleep(0.2)
    yield 'updating {} / {}'.format(len(complete), len(sources))
    while len(patch_group):
        try:
            x = y.get(timeout=1)
        except:
            continue
        yield x

    patch_group.join()
    
    # start the patch loop
    patch_loop_greenlet = gevent.spawn(patch_loop)

def terminate():
    patch_group.join()
    if patch_loop_greenlet:
        try:
            patch_loop_greenlet.kill()
        except AssertionError:
            pass
# interface

@interface.register
class ExternalSource(interface.Interface):
    name = "patch"

    @interface.protected
    def add_source(url=None):
        if add_source(url):
            gevent.spawn_later(1, patch_all)
            return True
        return False

    @interface.protected
    def modify_source(update=None, **filter):
        with transaction:
            filter_objects_callback([s for s in sources.values() if isinstance(s, PublicSource)], filter, lambda obj: obj.modify_table(update))

    def check_source(**filter):
        def check(obj):
            with transaction:
                obj.enabled = True
            gevent.spawn(obj.check)
        filter_objects_callback([s for s in sources.values() if isinstance(s, PublicSource)], filter, check)

    def patch_all():
        patch_all()
    
    @interface.protected
    def remove_source(erase=True, **filter):
        filter_objects_callback([s for s in sources.values() if isinstance(s, PublicSource)], filter, lambda obj: obj.delete(erase))
    
    @interface.protected
    def sync_sources(clients=None):
        for source in sources.values():
            data = dict(url=source.get_repo_url())
            for client in clients:
                if client == settings.app_uuid:
                    continue
                proto.send('client', 'patch.add_source', payload=data, channel=client)

    def version():
        return dict(version=core_source.version)
