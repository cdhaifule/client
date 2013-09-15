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
import json
import base64
import gevent
import hashlib
import itertools

from gevent.lock import RLock
from collections import defaultdict

from .. import logger, event, interface, speedregister, hoster, seekingfile, settings
from ..config import globalconfig
from ..contrib import sizetools
from ..scheme import Table, Column, transaction, get_by_uuid
from ..plugintools import ErrorFunctions, InputFunctions, GreenletObject, filesystemencoding, Url, auto_generate_filename


########################## config

config = globalconfig.new('core')

# download options
config.default('download_dir', os.path.join(settings.home_dir, u'Downloads', u'Download.am'), unicode)
config.default('complete_dir', config.download_dir, unicode)
config.default('create_package_folders', False, bool)
config.default('add_part_extension', True, bool)

# extract options
config.default('autoextract', True, bool)
config.default('extract_dir', config.download_dir, unicode)
config.default('bruteforce_passwords', list(), list)
config.default('delete_extracted_archives', False, bool)

# misc options
config.default('removed_completed', 'never', str) # never|package|file
config.default('open_browser_after_add_links', False, bool)

# adult
config.default('adult', False, bool)


########################## log and lock

log = logger.get('core')
lock = RLock()


########################## main collections

_packages = list()
packages = lambda: iter(_packages)
files = lambda: itertools.chain(*[iter(package.files) for package in packages()])


########################## convert names for package and file

def convert_name(system, value):
    if not isinstance(value, basestring):
        raise ValueError("name must be string")
    if system != 'torrent': # remove slashes when this is not a torrent
        value = re.sub(r'[/\\]', '_', value)
    if sys.platform == 'win32':
        for c in '/:*?"<>|':
            value = value.replace(c, '_')
    return value


########################## global status

class GlobalStatus(Table):
    _table_name = 'global_status'

    id = Column('api')
    tabs = Column('api', always_use_getter=True, getter_cached=True)
    packages = Column('api')
    packages_working = Column('api')
    files = Column('api', always_use_getter=True, getter_cached=True)
    files_working = Column('api', always_use_getter=True, getter_cached=True)
    chunks = Column('api', always_use_getter=True, getter_cached=True)
    chunks_working = Column('api', always_use_getter=True, getter_cached=True)
    size = Column('api', always_use_getter=True, getter_cached=True)
    _progress = Column(always_use_getter=True, getter_cached=True)
    progress = Column('api', always_use_getter=True, getter_cached=True, change_affects=['_progress'])
    speed = Column('api', always_use_getter=True, getter_cached=True)
    eta = Column('api', always_use_getter=True, getter_cached=True)

    def __init__(self):
        self.id = 'global_status'

    def on_get_tabs(self, value):
        tabs = dict(collect=0, download=0, torrent=0, complete=0)
        for p in packages():
            tabs[p.tab] += 1
        return tabs

    def on_get_packages_working(self, value):
        return sum(1 for p in packages() if p.working)

    def on_get_files(self, value):
        return sum(len(p.files) for p in packages())

    def on_get_files_working(self, value):
        return sum(p.files_working for p in packages())

    def on_get_chunks(self, value):
        return sum(p.chunks for p in packages())

    def on_get_chunks_working(self, value):
        return sum(p.chunks_working for p in packages())

    def on_get_size(self, value):
        return sum(p.size for p in packages())

    def on_get__progress(self, value):
        ff = [f for f in files() if f.enabled and f._max_progress and f.progress]
        max_progress = sum(f._max_progress for f in ff)
        progress = sum(f.progress for f in ff) if max_progress else 0
        return max_progress, progress

    def on_get_progress(self, value):
        if self.files == 0:
            return
        max_progress, progress = self._progress
        return max_progress and progress/max_progress or None

    def on_get_speed(self, value):
        if not _packages:
            return 0
        return sum(p.speed or 0 for p in packages())

    def on_get_eta(self, value):
        if self.files == 0:
            return
        max_progress, progress = self._progress
        if not max_progress:
            return
        speed = self.speed
        if not speed:
            return
        remaining = max_progress - progress
        return int((remaining/speed)*1000)

with transaction:
    global_status = GlobalStatus()


######################## package

PACKAGE_POSITION_MULTIPLICATOR = 5

class Package(Table):
    _table_name = 'package'
    _table_collection = _packages
    _table_created_event = True
    _table_deleted_event = True

    id = Column(('db', 'api'), change_affects=[['global_status', 'packages']])
    name = Column(('db', 'api'), read_only=False, fire_event=True)
    download_dir = Column(('db', 'api'), read_only=False)
    complete_dir = Column(('db', 'api'), read_only=False)
    extract_dir = Column(('db', 'api'), read_only=False)
    extract = Column(('db', 'api'), read_only=False)             # value of None means global setting
    extract_passwords = Column(('db', 'api'), read_only=False)
    position = Column(('db', 'api'), read_only=False, fire_event=True)
    enabled = Column('api', fire_event=True, change_affects=['tab'])       # dummy variable needed for frontend
    state = Column(('db', 'api'), change_affects=['tab'], fire_event=True) # collect, download, (extract), complete
    tab = Column('api', always_use_getter=True, getter_cached=True, change_affects=[['global_status', 'tabs']])

    system = Column(('db', 'api'))

    hosts = Column('api', always_use_getter=True) # , getter_cached=True)

    last_error = Column(('db', 'api'))
    global_status = Column()

    size = Column('api', always_use_getter=True, getter_cached=True, change_affects=['eta', ['global_status', 'size']])
    _progress = Column(always_use_getter=True, getter_cached=True)
    progress = Column('api', always_use_getter=True, getter_cached=True, change_affects=['speed', 'eta', '_progress', ['global_status', 'progress']])
    speed = Column('api', always_use_getter=True, getter_cached=True, change_affects=['eta', ['global_status', 'speed']])
    eta = Column('api', always_use_getter=True, getter_cached=True, change_affects=[['global_status', 'eta']])
    files = Column(None, change_affects=[['global_status', 'files'], 'hosts', 'size', 'progress', 'tab'])
    files_working = Column(None, always_use_getter=True, getter_cached=True, change_affects=[['global_status', 'files_working'], 'tab', 'working'])
    chunks = Column('api', always_use_getter=True, getter_cached=True, change_affects=[['global_status', 'chunks']])
    chunks_working = Column('api', always_use_getter=True, getter_cached=True, change_affects=[['global_status', 'chunks_working']])

    working = Column('api', always_use_getter=True, getter_cached=True, change_affects=['tab', ['global_status', 'packages_working']])

    _torrent_hash = None
    _log = None

    def __init__(self, extract_passwords=None, position=None, state='collect', system='download', **kwargs):
        self.files = []
        self.global_status = global_status

        for k, v in kwargs.iteritems():
            if k != 'id':
                setattr(self, k, v)

        self.extract_passwords = extract_passwords or []
        self.state = state
        self.system = system

        if self.enabled is None:
            self.enabled = True

        position = position if position else 1 + (len(_packages) + 1)*PACKAGE_POSITION_MULTIPLICATOR
        self.set_position(position)

    ####################### column getter

    def on_get_hosts(self, value):
        return self.files and list(set(file.host.name for file in self.files if file.host)) or list()
    
    def on_get_size(self, value):
        return sum((f.get_any_size() or 0) for f in self.files if f.enabled) if self.files else 0

    def on_get__progress(self, value):
        files = {f.get_download_file(): f for f in self.files if f._max_progress and f.enabled}.values()
        max_progress = sum(f._max_progress for f in files)
        progress = sum(f.progress for f in files) if max_progress else 0
        return max_progress, progress
    
    def on_get_progress(self, value):
        if not self.files:
            return
        max_progress, progress = self._progress
        if max_progress:
            return float(progress)/max_progress
        return None
        
    def on_get_speed(self, value):
        if not self.files:
            return
        return sum(f.speed for f in self.files)

    def on_get_eta(self, value):
        if not self.files:
            return
        if self.state != 'download':
            return
        max_progress, progress = self._progress
        if not max_progress:
            return
        speed = self.speed
        if not speed:
            return
        remaining = max_progress - progress
        return int((remaining/speed)*1000)

    def on_get_files(self, value):
        return len(self.files)
    
    def on_get_files_working(self, value):
        return self.files and sum(1 for f in self.files if f.working) or 0
    
    def on_get_chunks(self, value):
        return self.files and sum(len(f.chunks) for f in self.files) or 0

    def on_get_chunks_working(self, value):
        return self.files and sum(f.chunks_working for f in self.files) or 0

    def on_get_working(self, value):
        #return any(True for f in self.files if f.working) if self.files else False
        return self.files_working > 0

    def on_get_tab(self, value):
        if self.state is None or self.state == 'collect' or self.files is None:
            return 'collect'
        if self.state == 'download':
            return self.system
        if not self.enabled or self.working or any(True for f in self.files if f.enabled and not f.state.endswith('_complete')):
            return self.system
        return 'complete'

    ####################### properties

    @property
    def log(self):
        if self._log is None:
            self._log = logger.get("package {}".format(self.id))
        return self._log

    ####################### name/position/sort

    def on_set_name(self, value):
        return convert_name(self.system, value)

    def set_position(self, position):
        if position != self.position:
            with transaction:
                self.position = position

    def _sort_files(self):
        with transaction:
            def _cmp(a, b):
                c = cmp(a[0:2], b[0:2])
                return -c if c else cmp(a[2:], b[2:])

            weights = {f.id: f.host.weight(f) if self.tab != 'complete' else 0 for f in self.files}
            self.files.sort(key=lambda f: (f.weight, weights[f.id], f.name, f.host.name, f.id, f), cmp=_cmp)

            # sort for frontend
            pos = dict()
            for i, f in enumerate(self.files):
                pos[f] = i
            frontend_sorted = sorted(self.files, key=lambda f: (f.name, pos[f]))
            for i, f in enumerate(frontend_sorted):
                f.position = i

    ####################### actions

    def activate(self):
        with transaction:
            self.last_error = None
            for f in self.files:
                f.activate(_package=True)

    def deactivate(self):
        with transaction:
            for f in self.files:
                f.deactivate(_package=True)

    def stop(self):
        t = False
        with transaction:
            for file in self.files:
                t = file.stop(_package=True) or t
        #if t:
        #    self.log.debug('stopped')
        return t

    def reset(self):
        with transaction:
            for file in self.files:
                file.reset(_package=True)
        event.fire('package:reset', self)

    def delete(self):
        if self.state == 'deleted':
            return
        with lock:
            with transaction:
                self.state = 'deleted'
                for file in self.files[:]:
                    file.delete(_package=True)
                self.table_delete()
            #self.log.debug('deleted')

    def erase(self):
        paths = [(self.get_download_path(), config.download_dir), (self.get_complete_path(), config.complete_dir)]
        with transaction:
            for file in self.files[:]:
                file.erase(_package=True)
        for path in paths:
            if path[0] != path[1] and path[1] is not None:
                try:
                    os.rmdir(path[0])
                except OSError:
                    pass
        self.delete()

    ####################### paths
    @filesystemencoding
    def get_download_path(self):
        """gets and sets the download directory"""
        if not self.download_dir:
            with transaction:
                self.download_dir = config['download_dir']
                if self.name and config['create_package_folders']:
                    self.download_dir = os.path.join(self.download_dir, self.name)
        return self.download_dir
        
    @filesystemencoding
    def get_complete_path(self):
        """gets and sets the complete directory"""
        if not self.complete_dir:
            with transaction:
                self.complete_dir = config['complete_dir'] or config['download_dir']
                if self.name and config['create_package_folders']:
                    self.complete_dir = os.path.join(self.complete_dir, self.name)
        return self.complete_dir
    
    @filesystemencoding
    def get_extract_path(self):
        """gets and sets the extract directory"""
        if not self.extract_dir:
            with transaction:
                self.extract_dir = config['extract_dir'] or config['download_dir']
                if self.name and config['create_package_folders']:
                    self.extract_dir = os.path.join(self.extract_dir, self.name)
        return self.extract_dir

    ####################### clone package

    def clone_empty(self, **kwargs):
        defaults = dict(
            name=self.name,
            download_dir=self.download_dir,
            complete_dir=self.complete_dir,
            extract_dir=self.extract_dir,
            extract=self.extract,
            extract_passwords=self.extract_passwords,
            system=self.system,
            state=self.state)
        defaults.update(kwargs)
        return Package(**defaults)

    ####################### split package by key

    allowed_split_keys = dict(
        host=lambda file: file.host.name,
        file_extension=lambda file: file.name and os.path.splitext(file.name)[1][1:]
    )

    def split(self, key):
        if key not in self.allowed_split_keys:
            raise ValueError('invalid key: {}'.format(key))
        func = self.allowed_split_keys[key]

        targets = defaultdict(list)
        for file in self.files:
            value = func(file)
            if value is None:
                continue
            if value:
                value = u' - {}'.format(value)
            # TODO: handle files with name = None
            if self.name.endswith(value):
                continue
            targets[value].append(file)

        with transaction:
            for value, files in targets.iteritems():
                name = u'{}{}'.format(self.name, value)
                for p in packages():
                    if p.name == name and p.tab == self.tab:
                        target_package = p
                        break
                else:
                    target_package = self.clone_empty(name=name, download_dir=None, complete_dir=None, extract_dir=None)
                for file in files:
                    file.package = target_package

    ####################### ...

    def __repr__(self):
        return 'Package<{id}, name={name}, position={position}, state={state}, files={files}, last_error={last_error}>'.format(
            id=self.id,
            name=repr(self.name),
            position=self.position,
            state=self.state,
            files=len(self.files),
            last_error=self.last_error)


######################## file

class File(Table, ErrorFunctions, InputFunctions, GreenletObject):
    _table_name = 'file'
    _table_created_event = True
    _table_deleted_event = True

    id = Column(('db', 'api'), change_affects=[['global_status', 'files']])
    package = Column(('db', 'api'), fire_event=True, foreign_key=[Package, 'files', lambda self, package: package.delete()], change_affects=[['package', 'files']])
    name = Column(('db', 'api'), getter_cached=True)
    size = Column(('db', 'api'), change_affects=[['package', 'size'], 'eta'], fire_event=True)
    position = Column(('db', 'api'), fire_event=True)
    state = Column(('db', 'api'), fire_event=True, change_affects=[['package', 'tab']])   # check, collect, download, download_complete, (extract, extract_complete), complete
    enabled = Column(('db', 'api'), fire_event=True, read_only=False, change_affects=['speed', 'name', 'working', ['package', 'tab'], ['package', 'size']])
    last_error = Column(('db', 'api'), change_affects=['name', 'working'], fire_event=True)
    completed_plugins = Column(('db', 'api'))

    substate = Column('api', getter_cached=True, change_affects=['next_try', 'last_error'])
    last_substate = None

    url = Column(('db', 'api'), getter_cached=True)
    extra = Column('db')
    referer = Column('db')
    hash_type = Column('db')
    hash_value = Column('db')
    approx_size = Column('db', change_affects=['size'], fire_event=True)
    weight = Column('db')

    host = Column('api', change_affects=['domain', ['package', 'hosts']], fire_event=True)
    domain = Column('api', always_use_getter=True, getter_cached=True)

    chunks = Column('api', change_affects=['chunks_working', ['package', 'chunks']], fire_event=True)
    chunks_working = Column('api', always_use_getter=True, getter_cached=True, change_affects=[['package', 'chunks_working']])

    progress = Column('api', change_affects=['speed', 'eta', ['package', 'progress']])
    _max_progress = None
    speed = Column('api', always_use_getter=True, getter_cached=True, change_affects=['eta', ['package', 'speed']])
    _last_speed = False
    eta = Column('api', always_use_getter=True, getter_cached=True)

    waiting = Column(None, fire_event=True)
    next_try = Column('api', fire_event=True)
    need_reconnect = Column(None, fire_event=True)
    input = Column(None, fire_event=True)

    greenlet = Column(None, change_affects=['working'])
    working = Column('api', always_use_getter=True, getter_cached=True, change_affects=['speed', ['package', 'files_working']])

    global_status = Column()

    # variables only for download
    account = None
    proxy = None
    download_func = None
    download_next_func = None
    can_resume = True
    max_chunks = None

    # internal variables
    _log = None
    _split_url = None
    _filehandle = None

    def set_download_context(self, account=None, proxy=None, download_func=None, download_next_func=None, can_resume=None, max_chunks=None):
        if account is not None:
            self.account = account
        if proxy is not None:
            self.proxy = proxy
        if download_func is not None:
            self.download_func = download_func
        if download_next_func is not None:
            self.download_next_func = download_next_func
        if can_resume is not None:
            self.can_resume = can_resume
        if max_chunks is not None:
            self.max_chunks = max_chunks

    # misc
    file_plugins_complete = False

    def __init__(self, package=None, enabled=True, state='check', url=None, host=None, pmatch=None, completed_plugins=None, weight=0, **kwargs):
        GreenletObject.__init__(self)
        if host:
            self.host = host
            self.pmatch = pmatch
            if self.pmatch is None:
                self.pmatch = self.host.match(self.url, self.split_url)
        else:
            try:
                self.host, self.pmatch = hoster.find(url)
            except TypeError:
                self.table_delete()
                return

        self.global_status = global_status

        if not isinstance(package, Package):
            package = get_by_uuid(package)
            if not isinstance(package, Package):
                raise ValueError('file.package must be a Package instance, got {}'.format(package))
        self.package = package

        self.chunks = list()
        if not completed_plugins:
            self.completed_plugins = set()
        else:
            self.completed_plugins = set(completed_plugins)

        self.url = url
        self.state = state
        self.weight = weight
        self.substate = [None]
        self.last_substate = list()
        self.enabled = enabled
        self.intern = None

        if not self.host:
            raise ValueError('file.host can not be null')

        kwargs.setdefault("name", None)
        for k, v in kwargs.iteritems():
            setattr(self, k, v)

        try:
            self.url, extra = self.url.rsplit("&---extra=", 1)
        except ValueError:
            extra = None
        if extra is not None:
            self.extra = json.loads(base64.urlsafe_b64decode(extra.encode("ascii")))

        self._speed = speedregister.SpeedRegister()

        self.retry_num = 0

    ####################### column setter
    
    def on_set_url(self, value):
        self._split_url = None
        return value
            
    def on_set_name(self, value):
        if value is None:
            return
        value = convert_name(self.package.system, value)
        return value

    def on_set_hash_type(self, value):
        return value and value.lower() or None
        
    def on_set_hash_value(self, value):
        if not value:
            return None
        return value
                
    def on_set_size(self, value):
        value = isinstance(value, basestring) and sizetools.human2bytes(value) or value
        self._on_init_progress(value, self.state, value or self.approx_size)
        return value

    def on_set_approx_size(self, value):
        value = isinstance(value, basestring) and sizetools.human2bytes(value) or value
        self._on_init_progress(value, self.state, self.size or value)
        return value

    def on_set_state(self, value):
        self._on_init_progress(value, value, self.get_any_size())
        self.retry_num = 0
        if value == 'download_complete' and 'download' not in self.completed_plugins:
            with transaction:
                self.completed_plugins.add('download')
        return value

    def _on_init_progress(self, ctx, state, size):
        if state == 'download':
            if size is not None and size != self._max_progress:
                self.init_progress(size)
        return ctx

    ####################### column getter

    def on_get_name(self, name):
        if self.last_error and name is None:
            return auto_generate_filename(self)
        else:
            return name

    def on_get_url(self, url):
        return hashlib.md5(url).hexdigest()

    def on_get_package(self, package):
        return package and package.id or None

    def on_get_size(self, size):
        return size or self.approx_size

    def on_get_host(self, host):
        return host and host.name or None

    def on_get_domain(self, value):
        return self.host and self.host.get_hostname(self) or None

    def on_get_substate(self, substate):
        priority = {
            'init': 1,
            'retry': 2,
            'waiting_account': 3,
            'waiting': 4,
            'input': 5,
            'running': 6}
        get_priority = lambda s: s and priority.get(s[0], 0) or 0
        substate = substate, get_priority(substate)
        none_exists = False
        for chunk in self.chunks:
            p = get_priority(chunk.substate)
            if chunk.substate[0] is None and chunk.working:
                none_exists = True
            if substate[1] < p:
                substate = chunk.substate, p
        if none_exists and substate[0] == 'init':
            return [None]
        return substate[0]

    def on_get_next_try(self, value):
        if self.substate[0] == 'waiting_account':
            value = self.account.next_try
        return None if value is None else int(value.eta*1000)

    def on_get_last_error(self, value):
        if self.substate[0] == 'waiting_account':
            value = u'{}: {}'.format(self.account.name, self.account.last_error)
        return value

    def on_get_chunks(self, value):
        return self.chunks and len(self.chunks) or 0

    def on_get_chunks_working(self, value):
        return self.chunks and len(filter(lambda c: c.working, self.chunks)) or 0

    def on_get_progress(self, progress):
        return progress and self._max_progress and progress/self._max_progress or 0.0

    def on_get_speed(self, speed):
        return self.greenlet and self.enabled and self._speed.get_bytes() or 0

    def on_get_eta(self, eta):
        if not self.greenlet:
            return
        if self.state != 'download':
            return
        if not self._max_progress:
            return
        if self.progress is None:
            return
        speed = self.speed
        if not speed:
            return
        remaining = self._max_progress - self.progress
        return int((remaining/speed)*1000)

    def on_set_greenlet(self, value):
        return value

    def on_get_working(self, value):
        if not self.greenlet:
            return False
        #if isinstance(self.greenlet, gevent.Greenlet) and self.greenlet.dead:
        #    return False
        return True

    ####################### greenlet changed

    def on_greenlet_started(self):
        event.fire('file:greenlet_start', self)

    def on_greenlet_stopped(self):
        event.fire('file:greenlet_stop', self)
        self._speed.reset()
        with transaction:
            self.set_column_dirty('speed')

    ####################### properties

    @property
    def log(self):
        if self._log is None:
            self._log = logger.get("file {}".format(self.id))
        return self._log

    @property
    def split_url(self):
        if self._split_url is None:
            self._split_url = Url(self.url)
        return self._split_url

    @property
    def hashed_url(self):
        h = hashlib.new('md5')
        h.update(self.url)
        return h.hexdigest()

    def get_any_size(self):
        return self.size or self.approx_size or None

    @property
    def filehandle(self):
        with lock:
            if self._filehandle is None:
                path = self.get_download_file(create_dirs=True)
                self._filehandle = seekingfile.SeekingFile(path, size=self.size)
            return self._filehandle

    ####################### paths

    @filesystemencoding
    def get_download_path(self, create_dirs=False):
        path = self.package.get_download_path()
        if create_dirs and not os.path.exists(path):
            os.makedirs(path)
        return path

    @filesystemencoding
    def _get_fs_name(self):
        return self.name
    
    @filesystemencoding
    def get_download_file(self, create_dirs=False):
        path = self.get_download_path(create_dirs)
        if self.name:
            path = os.path.join(path, self._get_fs_name())
        else:
            path = path
        #if self.package.system == 'download':
        if config.add_part_extension:
            path += '.dlpart'
        return path
    
    @filesystemencoding
    def get_complete_path(self, create_dirs=False):
        path = self.package.get_complete_path()
        if create_dirs and not os.path.exists(path):
            os.makedirs(path)
        return path
    
    @filesystemencoding
    def get_complete_file(self, create_dirs=False):
        path = self.get_complete_path(create_dirs)
        if self.name:
            return os.path.join(path, self._get_fs_name())
        return path

    @filesystemencoding
    def get_extract_path(self, create_dirs=False):
        path = self.package.get_extract_path()
        if create_dirs and not os.path.exists(path):
            os.makedirs(path)
        return path

    ####################### progress

    def init_progress(self, max, init=0.0):
        self._max_progress = float(max)
        if self.progress != init:
            with transaction:
                self.progress = float(init)

    def add_progress(self, current):
        if self.progress is not None:
            with transaction:
                self.progress += current

    def set_progress(self, current):
        if self.progress is not None:
            with transaction:
                if current < 0:
                    self.progress = self._max_progress + current
                else:
                    self.progress = current

    def reset_progress(self):
        self._max_progress = None
        if self.progress is not None:
            with transaction:
                self.progress = None

    ####################### speed

    def register_speed(self, value):
        self._speed.register(value)
        speedregister.globalspeed.register(value)

    ####################### wait/retry/fatal

    def wait(self, seconds):
        seconds = float(seconds)
        self.log.info(u'waiting {} seconds'.format(seconds))
        with transaction:
            self.waiting = time.time() + seconds
        try:
            time.sleep(seconds)
        finally:
            with transaction:
                self.waiting = None

    def retry(self, msg, seconds, need_reconnect=False):
        seconds = float(seconds)
        if self.input:
            interface.call('input', 'abort', id=self.input.id)
            self.input = None
        with transaction:
            self.last_error = msg
            self.need_reconnect = need_reconnect
            self.next_try = gevent.spawn_later(seconds, self.reset_retry)
            self.next_try.eta = time.time() + seconds
            self.retry_num += 1
        self.log.info(u'retry in {} seconds: {}; reconnect: {}'.format(seconds, msg, need_reconnect))
        event.fire('file:retry', self)
        raise gevent.GreenletExit()

    def reset_retry(self, fire_event=True):
        g = None
        with transaction:
            self.last_error = None
            self.need_reconnect = False
            if self.next_try:
                g = self.next_try
                self.next_try = None
        if fire_event:
            event.fire('file:retry_timeout', self)
        if g:
            g.kill()

    def fatal(self, msg, abort_greenlet=True):
        if self.input:
            interface.call('input', 'abort', id=self.input.id)
            self.input = None
        with transaction:
            self.last_error = msg
            self.enabled = False
            self.log.error(msg)
        event.fire('file:fatal_error', self)
        if abort_greenlet:
            raise gevent.GreenletExit()

    ####################### actions

    def activate(self, _package=False):
        if self.last_error and self.last_error.startswith('downloaded via'):
            return
        self.reset_retry()
        with transaction:
            self.enabled = True

    def deactivate(self, _package=False):
        with transaction:
            self.enabled = False

    def stop(self, _package=True, _stop_fileplugins=True):
        self.retry_num = 0
        t = False
        with transaction:
            if self.next_try:
                self.next_try.kill()
                self.next_try = None
                t = True
            if _stop_fileplugins and 'download' in self.completed_plugins:
                # don't stop fileplugin greenlets
                #self.log.debug('skipping stop, state is at {}'.format(self.state))
                return False
            if self.greenlet:
                self.kill()
                t = True
            for chunk in self.chunks:
                t = chunk.stop() or t
            if self.input:
                interface.call('input', 'abort', id=self.input.id)
                self.input = None
            self.substate = [None]
            self.last_substate = list()
        #if t:
        #    self.log.debug('stopped')
        event.fire('file:stopped', self)
        #self.log.debug('stopped')
        return t

    def reset(self, _package=False, _inner_reset=False):
        with transaction:
            self.stop(_package=_package, _stop_fileplugins=True)
            self.reset_progress()
            self.delete_chunks()

            self.account = None
            self.proxy = None
            self.download_func = None
            self.download_next_func = None
            self.can_resume = True
            self.max_chunks = None

            if self.package.system == 'download':
                self.state = 'check'
            else:
                if self.package.state == 'collect':
                    self.state = 'collect'
                else:
                    self.state = 'download'

            if not _inner_reset or (self.last_error is not None and self.last_error.startswith('downloaded via ')):
                self.enabled = True
                self.last_error = None
            if self.next_try:
                self.next_try.kill()
                self.next_try = None
            self.need_reconnect = False
            self.completed_plugins = set()

            if self.package.state not in ('collect', 'download'):
                self.package.state = 'download'

            if _inner_reset:
                return

            download_file = self.get_download_file()
            for file in files():
                if file != self and file.get_download_file() == download_file:
                    file.reset(_package=_package if file.package == self.package else False, _inner_reset=True)

        self.delete_local_files()

    def delete_chunks(self):
        for chunk in self.chunks[:]:
            chunk.table_delete()
        self.chunks = []

    def delete(self, _package=False):
        if self.state == 'deleted':
            return
        with transaction:
            self.stop()
            tempfile = self.get_download_file()
            complete = self.get_complete_file()
            self.state = 'deleted'
            # push changes to package
            self.size = None
            self.approx_size = None
            self.progress = 0
            # delete chunks...
            for chunk in self.chunks[:]:
                chunk.delete()
            with lock:
                self.table_delete()
        #self.log.debug('deleted')
        if tempfile != complete and os.path.exists(tempfile):
            try:
                os.remove(tempfile)
            except (IOError, OSError) as e:
                self.log.warning("could not delete temporary file {}: {}".format(tempfile, e))

    def delete_after_greenlet(self):
        self.run_after_greenlet(self.delete)

    def erase_after_greenlet(self):
        self.run_after_greenlet(self.erase)

    def erase(self, _package=False):
        if self.state == 'deleted':
            return
        with transaction:
            self.reset(_package=_package)
            self.delete(_package=_package)

    ####################### errors

    def set_offline(self, msg=None):
        event.fire('file:offline', self)
        self._dhf_error(msg or 'offline')

    ####################### "physical" file functions

    def check_hdd_file(self):
        """checks if file (still) exists on hdd and resets chunks if not"""
        if self.chunks:
            path = self.get_download_file()
            if not os.path.exists(path):
                self.log.warning(u'file not found on hdd. resetting chunks')
                self.delete_chunks()

    def delete_local_files(self):
        def _remove(fn, path):
            if os.path.exists(path):
                try:
                    fn(path)
                    return True
                except OSError:
                    return False

        path = self.get_download_file()
        if os.path.exists(path):
            _remove(os.unlink, path)
        if path.endswith('.dlpart'):
            path = os.path.splitext(path)[0]
            _remove(os.unlink, path)

        if self.name is not None:
            path = self.get_complete_file()
            _remove(os.unlink, path)

            download_path = self.get_download_path()
            complete_path = self.get_complete_path()

            while True:
                path = os.path.split(self.name)[0]
                if not path:
                    break
                b = False
                for path in [os.path.join(download_path, path), os.path.join(complete_path, path)]:
                    b = _remove(os.rmdir, path) or b
                if b is False:
                    break

    ####################### compare this file with another

    def match_file(self, file):
        if self.name != file.name:
            return False
        if self.size and file.size and self.size != file.size:
            return False
        if self.approx_size and file.approx_size and int(self.approx_size/1024) != int(file.approx_size/1024):
            return False
        if self.size and file.approx_size and int(self.size/1024) != int(file.approx_size/1024):
            return False
        if self.approx_size and file.size and int(self.approx_size/1024) != int(file.size/1024):
            return False
        return True

    ####################### substate

    def set_substate(self, *args):
        with transaction:
            self.substate = args or [None]
            self.last_substate = list()

    def push_substate(self, *args):
        with transaction:
            if self.substate[0] is not None and (not self.last_substate or self.last_substate[-1][0] != self.substate[0]):
                self.last_substate.append(self.substate)
            self.substate = args or [None]

    def pop_substate(self, *args):
        if self.last_substate:
            with transaction:
                self.substate = self.last_substate.pop()
        elif self.substate != [None]:
            with transaction:
                self.substate = [None]

    ####################### check function (package assign ...)

    def set_infos(self, *args, **kwargs):
        from ..check import set_infos
        return set_infos(self, *args, **kwargs)

    def __repr__(self):
        return u'File<{id}, name={name}, size={size}, approx_size={approx_size}, state={state}, chunks={chunks}, last_error={last_error}>'.format(
            id=self.id,
            name=repr(self.name),
            size=self.size,
            approx_size=self.approx_size,
            state=self.state,
            chunks=len(self.chunks),
            last_error=self.last_error)


######################## chunk

class Chunk(Table, ErrorFunctions, InputFunctions, GreenletObject):
    _table_name = 'chunk'

    id = Column('db')
    file = Column('db', foreign_key=[File, 'chunks'])
    begin = Column('db')
    end = Column('db')
    pos = Column('db')
    state = Column('db', fire_event=True)           # download, complete

    substate = Column(None, change_affects=[['file', 'substate']])
    last_substate = None

    waiting = Column(None)
    next_try = Column(None)
    need_reconnect = Column(None)
    input = Column(None)

    last_error = None
    greenlet = Column(None, change_affects=[['file', 'chunks_working']])

    _log = None

    def __init__(self, id=None, file=None, begin=0, end=None, pos=None, state='download'):
        GreenletObject.__init__(self)
        
        if not isinstance(file, File):
            try:
                file = get_by_uuid(file)
            except KeyError:
                self.table_delete()
                return
            if not isinstance(file, File):
                raise ValueError('chunk.file must be a File instance, got{}'.format(file))
        self.file = file

        self.begin = begin
        self.end = end
        self.pos = pos and pos or begin
        self.state = state
        self.substate = [None]
        self.last_substate = list()

    ####################### column getter

    def on_get_file(self, file):
        return file.id

    ####################### properties

    @property
    def log(self):
        if self._log is None:
            self._log = logger.get("chunk {}".format(self.id))
        return self._log

    @property
    def account(self):
        return self.file.account

    @property
    def url(self):
        return self.file.url

    @property
    def extra(self):
        return self.file.extra

    @property
    def referer(self):
        return self.file.referer

    @property
    def pmatch(self):
        return self.file.pmatch

    @property
    def working(self):
        if not self.greenlet:
            return False
        #if isinstance(self.greenlet, gevent.Greenlet) and self.greenlet.dead:
        #    return False
        return True

    ####################### wait/retry/fatal

    def wait(self, seconds):
        seconds = float(seconds)
        self.log.info(u'waiting {} seconds'.format(seconds))
        with transaction:
            self.waiting = time.time() + seconds
        try:
            time.sleep(seconds)
        finally:
            with transaction:
                self.waiting = None

    def retry(self, msg, seconds, need_reconnect=False):
        seconds = float(seconds)
        self.log.info(u'retry in {} seconds: {}; reconnect: {}'.format(seconds, msg, need_reconnect))
        with transaction:
            self.last_error = msg
            self.need_reconnect = need_reconnect
            self.next_try = time.time() + seconds
            if self.input:
                interface.call('input', 'abort', id=self.input.id)
                self.input = None
        raise gevent.GreenletExit()

    def fatal(self, msg):
        if self.input:
            interface.call('input', 'abort', id=self.input.id)
            self.input = None
        self.file.fatal(msg)

    ####################### actions

    def stop(self):
        with transaction:
            self.next_try = None
            self.waiting = None
            if self.input:
                interface.call('input', 'abort', id=self.input.id)
                self.input = None
            self.substate = [None]
            self.last_substate = list()
            if self.greenlet:
                self.greenlet.kill()
                #self.log.debug('stopped')
                return True
            return False

    def delete(self):
        with transaction:
            self.stop()
            with lock:
                self.table_delete()

    def delete_after_greenlet(self):
        self.run_after_greenlet(self.delete)
    
    ####################### substate

    def set_substate(self, *args):
        with transaction:
            self.substate = args or [None]
            self.last_substate = list()

    def push_substate(self, *args):
        with transaction:
            if self.substate[0] is not None and (not self.last_substate or self.last_substate[-1][0] != self.substate[0]):
                self.last_substate.append(self.substate)
            self.substate = args or [None]

    def pop_substate(self, *args):
        with transaction:
            if self.last_substate:
                self.substate = self.last_substate.pop()
            else:
                self.substate = [None]
