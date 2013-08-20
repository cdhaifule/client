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
import json
import zlib
import gevent
import base64
import random
import difflib
import types

from . import core, event, plugintools, logger, cache, api
from .contrib import gibberishaes
from .scheme import transaction
from .config import globalconfig
from .variablesizepool import VariableSizePool

from gevent.pool import Pool
from gevent.lock import Semaphore

pool = Pool(size=40)
lock = Semaphore()
log = logger.get('check')

config = globalconfig.new('check')
config.default('max_retires', 3, int)
config.default('use_cache', False, bool)
config.default('get_cache_timeout', 5, int)

def init():
    pass

################################## hoster functions

def check_file(file):
    try:
        result = plugintools.ctx_error_handler(file, file.account.on_check_decorator, file.host.on_check, file)
        if isinstance(result, types.GeneratorType):
            result = list(result)
        if isinstance(result, list):
            added_links = core.add_links(result)
        elif isinstance(result, tuple):
            added_links = core.add_links(*result)
        elif isinstance(result, dict):
            added_links = core.add_links(**result)
        elif result is not None:
            raise RuntimeError('invalid return value of on_check: {}'.format(result))
        else:
            added_links = False

        if file.name is None:
            if added_links and file.get_any_size() is None and file.last_error is None:
                file.delete_after_greenlet()
            elif file.state != 'deleted' and not any(file.delete == fn[0] for fn in file._greenlet_funcs):
                file.fatal('link check was not successful (missing filename)', abort_greenlet=False)
        elif file.state == 'check' and file.last_error is None:
            with transaction:
                file.state = 'collect'
        if not added_links and result is not None:
            file.no_download_link()
        file.fire_after_greenlet('file:checked', file)
    finally:
        if not file._table_deleted:
            with transaction:
                file.reset_progress()
                file.set_progress(sum(chunk.pos - chunk.begin for chunk in file.chunks))

################################## db functions

def assign_by_file(file):
    for p in core.packages():
        if p.name is None:
            continue
        if p == file.package:
            continue
        if p.system == 'torrent':
            continue
        if p.state != 'collect':
            continue
        for f in p.files:
            if f.match_file(file):
                return f.package

def assign_convert_name(name):
    m = re.match(r"^(?P<name>.+)\.((part\d+)?\.rar|r\d{2}|\d{3}|(tar\.)?gz|gz)(\.html?)?$", name)
    if m:
        return m.group('name')
    name, _ = os.path.splitext(name)
    return name

SPECIAL_CHARS = (
    ('#', '#'),
    ('\-', '-'),
    ('/', '/'),
    ('\\\\', '\\\\'),
    ('\.', '.'),
    ('\s', ' '))

def get_diff_name(name, names):
    matches = difflib.get_close_matches(name, names)
    for match in matches:
        seq = difflib.SequenceMatcher(a=name, b=match)
        ratio = seq.quick_ratio()
        if ratio < 0.8:
            continue
        blocks = seq.get_matching_blocks()
        if len(blocks) > 4:
            continue
        result = ""
        for b in blocks:
            a = name[b.a:b.a+b.size]
            while result and a and result[-1] == a[0]:
                a = a[1:]
            result += a
        for a, b in SPECIAL_CHARS:
            result = re.sub(a+a+'+', b, result)
        result = result.rstrip(''.join(b for a, b in SPECIAL_CHARS))
        return match, result

def assign_by_name(name, old_package):
    candidates = dict((p.name, p) for p in core.packages() if p.name is not None and p.state == 'collect' and p.system != 'torrent')
    if name in candidates:
        return candidates[name]

    names = candidates.keys()
    try:
        names.remove(old_package.name)
    except ValueError:
        pass
    x = get_diff_name(name, names)
    if x:
        old_name, new_name = x
        package = candidates[old_name]

        if new_name in candidates:
            p = candidates[new_name]
            if len(package.files) < len(p.files):
                tmp = p
                p = package
                package = tmp
            p.log.debug('merging to package {}'.format(package.id))
            for f in p.files[:]:
                f.package = package

        package.name = new_name
    else:
        package = core.Package(name=name, extract_passwords=old_package.extract_passwords[:])
    return package

def _assign_file(file, name, prefix, postfix):
    #find a package
    #generate possible package name
    #TODO: more intelligent package name creation, attaching to existing packages ...
    if not name:
        package = assign_by_file(file)
        if package:
            return package

    if not name:
        name = assign_convert_name(file.name)
    #add pre and postfix
    if prefix:
        name = prefix+name
    if postfix:
        name = postfix+name
    #find package
    return assign_by_name(name, file.package)

def assign_file(file, name=None, prefix=None, postfix=None):
    package = _assign_file(file, name, prefix, postfix)
    if package.id != file.package.id:
        file.log.debug('assigning from package {} ({}) to {} ({})'.format(file.package.id, repr(file.package.name), package.id, repr(package.name)))
        file.package = package

def set_infos(file, name=None, size=None, approx_size=None, package_name=None, package_name_prefix=None, package_name_postfix=None, hash_type=None, hash_value=None, update_state=True):
    """this is the main function that have to be called after a check. it creates/assings files to a package ..."""
    with transaction:
        if name and file.name is None:
            file.name = name
        if name and file.name.find(".") < 0: # overwrite only if filename has no extension
            file.name = name
        if size:
            file.size = size
        if approx_size is not None:
            file.approx_size = approx_size

        if hash_type and hash_value:
            file.hash_type = hash_type
            file.hash_value = hash_value

        if file.package.system != 'torrent':
            #if file.name and (file.package.name is None or file.package.state == 'collect'):
            if file.name and file.package.name is None:
                with core.lock:
                    assign_file(file, package_name, package_name_prefix, package_name_postfix)

        if file.state == 'check' and update_state:
            if file.package.state != 'collect':
                file.state = 'download'
            else:
                file.state = 'collect'
    gevent.sleep(0)

################################## events

check_queue = list()

pool = VariableSizePool(size=20)

@event.register('file:retry_timeout')
@event.register('file.state:changed')
@event.register('file.last_error:changed')
def file_changed(e, file, old=None):
    if _file_ready(file):
        if file.retry_num > config.max_retires:
            file.fatal('check retries of {} exceeded'.format(config.max_retires), abort_greenlet=False)
        event.fire_once_later(0.5, 'check:spawn_tasks')

@event.register('file:greenlet_stop')
def file_stopped(e, file):
    event.fire_once_later(0.5, 'check:spawn_tasks')

################## spawn tasks

@event.register('check:spawn_tasks')
def spawn_tasks(e):
    core.sort_queue.wait()
    with lock, transaction:
        if config.use_cache and api.client.is_connected():
            files = list(_check_via_api())
        else:
            files = core.files()
        if pool.full():
            return
        for file in files:
            if not _file_ready(file):
                continue
            if not _file_pools_ready(file):
                continue
            _spawn_task(file)
            if pool.full():
                return

def _spawn_task(file):
    if file.retry_num > config.max_retires:
        file.fatal('check retries of {} exceeded'.format(config.max_retires), abort_greenlet=False)

    file.log.debug(u'checking {} via account {} {}'.format(file.url, file.account.name, file.account.id))
    file.spawn(check_file, file)
    pool.add(file.greenlet)
    file.host.check_pool.add(file.greenlet)
    file.account.check_pool.add(file.greenlet)
    return True

def _file_ready(file):
    if file.state != 'check':
        return False
    if not file.enabled:
        return False
    if file.working:
        return False
    if file.last_error:
        return False
    return True

def _file_pools_ready(file):
    if file.host.check_pool.full():
        return False

    file.account = file.host.get_account('check', file)
    if file.account.check_pool.full():
        return False

    return True

################## get/set remote file status cache

get_cache_results = dict()
check_cache = dict()
from_cache = set()
cache_ignore = set()

def _check_via_api():
    global cache_ignore

    files = dict()
    for file in core.files():
        if file.package.system != 'download':
            continue
        if not _file_ready(file):
            continue
        yield file # this file is maybe ready for a local check
        if random.randint(0, 9) == 0:
            continue
        if file.hashed_url in cache_ignore:
            continue
        if not file.host.use_check_cache:
            continue
        files[file.hashed_url] = file
    if not files:
        return

    keys = files.keys()
    log.info('asking remote file status cache for infos about {} files'.format(len(keys)))
    try:
        result = cache.get(keys)
    except BaseException as e:
        log.error('error getting file status cache result: {}'.format(e))
        return

    with transaction:
        for fid, data in result.iteritems():
            if data is None:
                continue
            if fid not in files:
                log.warning('found no file with id {} for file status cache'.format(fid))
                continue
            from_cache.add(fid)
            data = base64.b64decode(data)
            data = gibberishaes.decrypt(files[fid].url, data)
            data = zlib.decompress(data)
            data = json.loads(data)
            if 'offline' in data:
                try:
                    files[fid].set_offline(data['offline'])
                except gevent.GreenletExit:
                    pass
                del data['offline']
            if data:
                files[fid].set_infos(**data)
                event.fire('file:checked', files[fid])
            del files[fid]

    cache_ignore |= set(files.keys())

    log.info('got {} files from remote cache ({} not found)'.format(len(result), len(files)))

@event.register('file:checked')
@event.register('file:offline')
def send_file_status_to_cache(e, file):
    if not config.use_cache:
        return
    if not api.client.is_connected():
        return
    if not file.host.use_check_cache:
        return
    if file.package.system != 'download':
        return
    fid = file.hashed_url
    if fid in from_cache:
        from_cache.remove(fid)
        return
    if e == 'file:checked':
        data = dict()
        if file.name is not None:
            data['name'] = file.name
        if file.size is not None:
            data['size'] = file.size
        elif file.approx_size is not None:
            data['approx_size'] = file.approx_size
        if file.hash_type is not None:
            data['hash_type'] = file.hash_type
            data['hash_value'] = file.hash_value
    elif e == 'file:offline':
        data = dict(offline=file.last_error)

    data = json.dumps(data)
    data = zlib.compress(data)
    data = gibberishaes.encrypt(file.url, data)
    data = base64.b64encode(data)
    check_cache[file.hashed_url] = data
    event.fire_once_later(30, 'check:set_cache')

@event.register('check:set_cache')
def set_cache(e):
    global check_cache
    if check_cache:
        data = check_cache
        check_cache = dict()
        log.info('sending status of {} files to remote cache'.format(len(data)))
        cache.set(data)

################## global check progress

"""from .progress import Progress
with transaction:
    progress = Progress('check')

@event.register('file:deleted')
@event.register('file.enabled:changed')
@event.register('file.state:changed')
def _update_progress(e, *args):
    event.fire_once_later(0.5, 'check:update_progress')

@event.register('check:update_progress')
def update_progress(e):
    progress.set(
        sum(1 for f in core.files() if f.state in ('check', 'collect')),
        sum(1 for f in core.files() if f.state == 'collect' or (f.state == 'check' and not f.enabled)))
"""
