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

import gevent

from .engine import _packages, packages, files, lock, config, global_status, Package, File, Chunk, PACKAGE_POSITION_MULTIPLICATOR
from .. import event
from ..scheme import transaction
from ..plugintools import EmptyQueue


# update global_status

@event.register('package:created')
@event.register('package:deleted')
def on_package_changed(*args):
    with transaction:
        global_status.packages = len(_packages)


# sort functions

sort_queue = EmptyQueue()

@Package.position.changed
def package_position_changed(package, old):
    if 'packages' not in sort_queue:
        sort_queue.put('packages')
        event.fire_once_later(0.2, 'core:sort_queue')

@File.package.changed
@File.host.changed
@File.name.changed
def sort_package_files(file, old):
    if 'package' not in sort_queue:
        sort_queue.put('package')
        event.fire_once_later(0.2, 'core:sort_queue')

@event.register('account:created')
@event.register('account:initialized')
@event.register('account:deleted')
def sort_all_files(*args):
    if not 'files' in sort_queue:
        sort_queue.put('files')
        event.fire_once_later(0.2, 'core:sort_queue')

@event.register('core:sort_queue')
def handle_sort_queue(*args):
    with lock, transaction:
        if 'packages' in sort_queue:
            _packages.sort(lambda a, b: a.position - b.position)
            for i in range(len(_packages)):
                _packages[i].position = 1 + i*PACKAGE_POSITION_MULTIPLICATOR
            event.fire('packages:positions_updated')
        if 'files' in sort_queue:
            for package in packages():
                package._sort_files()
        elif 'package' in sort_queue:
            for package in packages():
                package._sort_files()
    sort_queue.clear()


# substate events

"""substate values:
default: null
while downloading:
    ['init']
    ['running']

while checking or downloading:
    ['input', input.id]
    ['waiting', miliseconds]
    ['retry', miliseconds, need_reconnect]"""

@File.waiting.changed
@Chunk.waiting.changed
def waiting_changed(self, value):
    if self.waiting:
        self.push_substate('waiting', int(self.waiting*1000))
    else:
        self.pop_substate()

@File.next_try.changed
@File.need_reconnect.changed
@Chunk.next_try.changed
@Chunk.need_reconnect.changed
def retry_changed(self, value):
    if self.next_try:
        if type(self.next_try) in (int, float):
            eta = self.next_try
        else:
            eta = self.next_try.eta
        self.push_substate('retry', int(eta*1000), self.need_reconnect)
    elif self.substate[0] != 'waiting_account':
        self.pop_substate()

@File.input.changed
@Chunk.input.changed
def input_changed(self, value):
    if self.input:
        self.push_substate('input', self.input.id)
    else:
        self.pop_substate()


# enable/disable files

@File.enabled.changed
def file_enabled_changed(file, old):
    if file.enabled:
        with transaction:
            if not file.package.enabled:
                file.package.enabled = True
            file.last_error = None
    else:
        if file.package.enabled and not any(f.enabled for f in file.package.files):
            with transaction:
                file.package.enabled = False
        gevent.spawn(file.stop)


# fileplugin dispatch complete

def _auto_remove_files(file):
    to_delete = list()
    path = file.get_complete_path()
    for f in files():
        if f.get_complete_path() == path:
            to_delete.append(f)
    with transaction:
        for f in to_delete:
            f.log.info('auto removing complete file')
            f.delete()

@event.register('fileplugin:done')
def fileplugins_done(e, path, file):
    if file is None:
        return
    file.file_plugins_complete = True
    if config.removed_completed == 'never':
        return
    elif config.removed_completed == 'file':
        _auto_remove_files(file)
    elif config.removed_completed == 'package':
        for f in file.package.files:
            if f.file_plugins_complete:
                continue
            if f.last_error is not None and not f.last_error.startswith('downloaded via'):
                return
        file.package.log.info('all plugins complete. deleting')
        with transaction:
            while file.package.files:
                _auto_remove_files(file.package.files[0])
            file.package.delete()


# file chunks changed (progress update)

@File.chunks.changed
def file_chunks_changed(file, old):
    if file.state == 'download' and file.package.system == 'download':
        size = file.get_any_size()
        if size is not None:
            file.init_progress(size)
            file.set_progress(sum(chunk.pos - chunk.begin for chunk in file.chunks))
