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
import os

from .engine import strategy, pool, lock, download_file, working_downloads, config
from .. import event, core, reconnect, api, account, input
from ..scheme import transaction

# states ... changed

@event.register('chunk.state:changed')
def chunk_state_changed(e, chunk, old):
    if chunk.state != 'download':
        chunk.speed = 0
    if chunk.state == 'complete':
        chunk.log.debug('download complete')

@event.register('file.state:changed')
@event.register('file.enabled:changed')
@event.register('file.last_error:changed')
def file_changed(e, file, old):
    if file.state == 'download' and file.package.system == 'download':
        if file.enabled and not file.last_error:
            event.fire('file:ready_for_download', file)

# handle the queue

@event.register('download:started')
@event.register('download.spawn_strategy:changed')
@event.register('account:initialized')
@event.register('reconnect:done')
@event.register('api:connected')
@event.register('file:ready_for_download')
@event.register('file:greenlet_stop')
def spawn_new_tasks_delayed(e, *args):
    event.fire_once_later(0.5, 'download:spawn_tasks')

@event.register('download:spawn_tasks')
def spawn_tasks(e):
    core.sort_queue.wait()
    with lock:
        if strategy.type == 'off':
            return
        if reconnect.manager.reconnecting:
            return
        if not api.is_connected():
            return
        if config.state != 'started':
            return
        blocked_hosts = set()
        for file in core.files():
            if _spawn_task(file, blocked_hosts):
                if pool.full():
                    return

def _spawn_task(file, blocked_hosts, retry=False):
    if file.state != 'download':
        return False
    if not file.enabled:
        return False
    if file.working:
        return False
    if file.package.system == 'torrent':
        return False
    if file.last_error:
        return False
    if file.host in blocked_hosts:
        return False
    if file.host.download_pool.full():
        blocked_hosts.add(file.host)
        return False

    file.host.get_download_context(file)
    if file.account is None or file.account.download_pool.full():
        blocked_hosts.add(file.host)
        return False

    if strategy.type == 'only_premium':
        if hasattr(file.account, 'premium') and not file.account.premium:
            blocked_hosts.add(file.host)
            return False

    # check if another mirror is already working
    path = file.get_download_file()
    for f in core.files():
        if f != file and ((f.state == 'download' and f.working) or ('download' in f.completed_plugins and not f.last_error)) and f.get_download_file() == path:
            return False

    if file.account.weight is None:
        if file.account.next_try and file.substate[0] != 'waiting_account':
            file.push_substate('waiting_account', file.account.id)
        return False
    elif file.substate and file.substate[0] == 'waiting_account':
        file.set_substate()

    path = file.get_complete_file()
    if os.path.exists(path):
        if config['overwrite'] == 'ask':
            elements = []
            elements.append(input.Text(u'The file {} already exists.'.format(file.name)))
            elements.append(input.Text('What do you want to do?'))
            elements.append(input.Input('remember', 'checkbox', default=False, label='Remember decision?'))
            elements.append(input.Choice('overwrite', choices=[
                {"value": 'overwrite', "content": "Overwrite"},
                {"value": 'rename', "content": "Rename"},
                {"value": 'skip', "content": "Skip"}]))
            try:
                result = input.get(elements, type='download.overwrite', parent=file)
            except input.InputTimeout:
                overwrite = 'skip'
            except input.InputAborted:
                file.fatal('user input aborted', abort_greenlet=False)
                return False
            except input.InputFailed as e:
                file.fatal('user input failed: {}'.format(e), abort_greenlet=False)
                return False
            else:
                overwrite = result['overwrite']
                if result.get('remember', False):
                    config['overwrite'] = overwrite
        else:
            overwrite = config['overwrite']
        
        if overwrite == 'rename':
            path = file.package.get_download_path()
            m = re.match(r"^(?P<name>.+)(?P<ext>\.(part\d+)?\.rar|r\d{2}|\d{3}|(tar\.)?gz|gz)$", file.name)
            if m:
                name, ext = m.group('name'), m.group('ext')
            else:
                name, ext = os.path.splitext(file.name)
            m = re.match(r'^(.*) \((\d+)\)$', name)
            if m:
                name, i = m.group(1), int(m.group(2))
                if i < 2:
                    i = 2
            else:
                i = 2
            path = file.package.get_complete_path()
            while True:
                n = '{} ({}){}'.format(name, i, ext)
                if not os.path.exists(os.path.join(path, n)):
                    file.log.info('renaming {} to {}'.format(file.name, n))
                    with transaction:
                        file.name = n
                    break
                i += 1
        elif overwrite == 'skip':
            file.fatal('file already exists', abort_greenlet=False)
            return False
        elif overwrite == 'overwrite':
            file.log.info('overwriting file {}'.format(path))
            try:
                os.unlink(path)
            except:
                pass

    if file.account is None: # this can happen during a race condition
        if retry:
            file.log.warning(u'account of file becomes null, even after retry. spawning file later')
            return False
        else:
            _spawn_task(file, blocked_hosts, True)

    file.log.debug(u'downloading {} via account {} {}'.format(file.url, file.account.name, file.account.id))

    working_downloads.append(file)
    with transaction:
        file.spawn(download_file, file)
    pool.add(file.greenlet)
    file.host.download_pool.add(file.greenlet)
    file.account.download_pool.add(file.greenlet)

    return True

# account retries

@account.Account.next_try.changed
def account_retry_changed(account, value):
    if account.next_try is None:
        for file in core.files():
            if file.substate and file.substate[0] == 'waiting_account' and file.substate[1] == account.id:
                file.set_substate()
                event.fire('file:ready_for_download', file)

@event.register('account:created')
def account_created(e, account):
    for file in core.files():
        if file.substate and file.substate[0] == 'waiting_account':
            if file.host.name == account.name:  # TODO: change this to some other good comparisation
                file.set_substate()
                event.fire('file:ready_for_download', file)

# account deleted/disabled event

@event.register('account:deleted')
def on_accounts_deleted(account, *args, **kwargs):
    on_account_changed(account)

@account.Account.enabled.changed
def on_account_changed(account, *args, **kwargs):
    if not account._table_deleted and account.enabled:
        return
    for f in working_downloads:
        if f.account == account and f.working:
            f.stop()

# reconnect

@event.register('file:download_task_done')
@event.register('config.reconnect.auto:changed')
@event.register('config.reconnect.method:changed')
def reconnect_changed(e, file, *args):
    if not reconnect.config['auto']:
        return
    if reconnect.manager.reconnecting:
        return
    if strategy.has('reconnect'):
        return

    need_reconnect = any(True for file in core.files() if file.need_reconnect)
    if need_reconnect:
        strategy.only_premium('reconnect', reconnect.reconnect)

@event.register('reconnect:success')
def reconnect_reconnecting(e):
    for f in working_downloads:
        f.stop()

# TODO: add event foo:ip_changed
@event.register('reconnect:success')
def reconnect_successful(e):
    for file in core.files():
        if file.need_reconnect:
            file.reset_retry()

# file retries

@event.register('file:retry_timeout')
def file_retry_timeout(e, file):
    if file.state == 'download':
        if file.retry_num > config.max_retires:
            file.fatal('download retries of {} exceeded'.format(config.max_retires), abort_greenlet=False)
        event.fire('file:ready_for_download', file)
