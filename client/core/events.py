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

import sys
import gevent
import traceback
import subprocess
from functools import partial

from .engine import _packages, packages, files, lock, config, global_status, Package, File, Chunk, PACKAGE_POSITION_MULTIPLICATOR
from .. import event, notify, input
from ..scheme import transaction
from ..plugintools import EmptyQueue


# update global_status

@event.register('package:created')
@event.register('package:deleted')
def on_package_changed(*args):
    with transaction:
        global_status.packages = len(_packages)

@event.register('file:deleted')
def on_file_deleted(e, file):
    p = file.package
    if p.state == 'download' and not any('download' not in f.completed_plugins for f in p.files):
        with transaction:
            p.state = 'download_complete'

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
    if value:
        self.push_substate('waiting', int(value*1000))
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
    #elif self.substate[0] != 'waiting_account':
    elif self.substate[0] == 'retry':
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
            file.last_error_type = None
    else:
        if file.package.enabled and not any(f.enabled for f in file.package.files):
            with transaction:
                file.package.enabled = False
        gevent.spawn(file.stop)


# fileplugin dispatch complete

def _auto_remove_files(file):
    to_delete = list()
    path = file.get_complete_file()
    for f in files():
        if f.get_complete_file() == path:
            to_delete.append(f)
    with transaction:
        for f in to_delete:
            f.log.info('auto removing complete file')
            f.delete()

@event.register('fileplugin:done')
def on_fileplugin_done(e, path, file):
    if file is None:
        return
    file.file_plugins_complete = True
    if config.removed_completed == 'never':
        return
    elif config.removed_completed == 'file':
        _auto_remove_files(file)
    elif config.removed_completed == 'package':
        if file.package.tab != 'complete':
            return
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

# shutdown computer when downloads are done

def tell_system_events(command):
    subprocess.call(
        ["osascript", "-e",
         """\
tell application "System Events"
    {}
end tell
""".format(command)])

def shutdown_windows():
    wmi.WMI(privileges=["Shutdown"]).Win32_OperatingSystem()[0].Shutdown()

default = ""
if sys.platform == "darwin":
    shutdown_actions = {
        "sleep": partial(tell_system_events, "sleep"),
        "shutdown": partial(tell_system_events, "shut down"),
    }
    default = "sleep"
elif sys.platform == "win32" and "nose" not in sys.argv[0]:
    try:
        import wmi
        wmi.WMI(privileges=["Shutdown"])
    except:
        shut_down_actions = {}
    else:
        shutdown_actions = {
            "shutdown": shutdown_windows,
        }
        default = "shutdown"
else:
    shutdown_actions = {}

if shutdown_actions:
    config.default("shutdown_action", default, str, enum=list(shutdown_actions), description="Select shutdown action")
    config.default('shutdown', False, bool, persistent=False)
    config.default('shutdown_timeout', 60, int, description="Display message box duration before computer shutdown")

def on_package_tab_changed(p, old):
    if p.tab not in {'collect', 'complete'}:
        return
    event.fire_once_later(1, 'core:check_package_tabs')

def on_engine_state_changed(*_):
    if check_engine_state():
        return

def check_engine_state():
    from .. import download, torrent
    if download.config.state == 'stopped' and torrent.config.state == 'stopped':
        config.shutdown = False
        notify.info('Computer shutdown disabled because download engine is stopped')
        return True

def check_package_tabs():
    for p in packages():
        if p.tab not in {'collect', 'complete'} and p.enabled:
            return False
    return True

@event.register('core:check_package_tabs')
def on_check_package_tabs(e):
    if not check_package_tabs():
        return
    config.shutdown = False
    elements = list()
    elements.append(
        [input.Text(
            ['All downloads are complete.\nThe computer will perform shut down command `#{command}` in #{seconds} seconds.',
             dict(command=config.shutdown_action, seconds=config.shutdown_timeout)])])
    elements.append([input.Text('')])
    elements.append([input.Choice('action', choices=[
        dict(value='abort', content='Abort shutdown', ok=True, cancel=True),
        dict(value='now', content='Shutdown now')
    ])])
    try:
        result = input.get(elements, type='computer_shutdown', timeout=config.shutdown_timeout, close_aborts=True)
        result = result.get('action', 'abort')
    except input.InputTimeout:
        result = 'now'
    except:
        traceback.print_exc()
        result = 'abort'
    if result == 'abort':
        notify.info('Computer shutdown aborted')
        return
    if result != 'now':
        raise RuntimeError('invalid return value from input: {}'.format(result))
    notify.warning('Computer will shutdown now')
    
    func = shutdown_actions.get(config.shutdown_action.strip())
    if func:
        func()
    else:
        notify.warning('no shutdown method')

@config.register('shutdown')
def on_config_shutdown_changed(value, old):
    if value:
        if check_engine_state():
            return
        if check_package_tabs():
            notify.info('Computer shutdown disabled because the download queue is empty')
            return
        if on_package_tab_changed not in Package.tab.changed_hooks:
            Package.tab.changed(on_package_tab_changed)
            event.add('config.download.state:changed', on_engine_state_changed)
            event.add('config.torrent.state:changed', on_engine_state_changed)
    else:
        if on_package_tab_changed in Package.tab.changed_hooks:
            Package.tab.remove_changed(on_package_tab_changed)
            event.remove('config.download.state:changed', on_engine_state_changed)
            event.remove('config.torrent.state:changed', on_engine_state_changed)
