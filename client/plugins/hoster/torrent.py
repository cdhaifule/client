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

import webbrowser
from gevent.event import Event

from ... import hoster, account, core, torrentengine, torrent, login

class Account(account.Account):
    _private_account = True

    def on_initialize(self):
        pass

@hoster.host
class this:
    model = hoster.Hoster
    account_model = Account
    name = "torrent"
    use_check_cache = False
    patterns = [
        hoster.Matcher('magnet'),
        hoster.Matcher('torrent')
    ]
    config = [
        hoster.cfg('magnet_timeout', 120, int, description='timeout for getting torrent over magnet link'),
        hoster.cfg('add', None, str, description='add magnet links without asking')
    ]

def can_download(file):
    return file.split_url.scheme == 'torrent'

def ask_user(file):
    if this.config.add:
        answer = this.config.add
    else:
        try:
            from ... import input
            elements = list()
            elements.append(input.Text("An external website wants to add a magnet link."))
            elements.append(input.Input('always_add', 'checkbox', default=True, label='Always add magnet links without asking.'))
            elements.append(
                input.Choice('answer', choices=[
                    {"value": "add", "content": "Add"},
                    {"value": "add_open", "content": "Add and open browser"},
                    {"value": "discard", "content": "Discard"}
                ]))
            result = input.get(elements, type='remember_boolean')
        except input.InputAborted:
            answer = 'discard'
        except input.InputTimeout:
            answer = 'add'
        else:
            answer = result.get("answer", "discard")
            if answer and result.get('always_add', False):
                this.config.add = answer
    if answer == 'add_open':
        webbrowser.open_new_tab(login.get_sso_url('collect'))
    print answer
    return answer in ('add', 'add_open')

def on_check(file):
    if file.split_url.scheme == 'torrent':
        return

    if not ask_user(file):
        return 'delete'

    e1 = Event()
    e2 = Event()

    def on_metadata_received_alert(alert):
        e1.wait()
        if str(alert.handle.info_hash()) == t.id:
            e2.set()

    torrentengine.session.register_alert('metadata_received_alert', on_metadata_received_alert)
    torrent.config.queue.active_downloads += 1
    try:
        try:
            t = torrentengine.Torrent(magnet=file.url, options=dict(save_path=core.config.download_dir, auto_managed=False))
        except RuntimeError:
            file.fatal('torrent already exists')
        except ValueError as e:
            file.fatal(str(e))

        try:
            name = t.name
            if name:
                if not name.endswith('.magnet'):
                    name += '.magnet'
                file.set_infos(name=name, update_state=False)

            t.resume()
            e1.set()
            if not e2.wait(timeout=this.config.magnet_timeout):
                file.fatal('error getting torrent from magnet link')
            t.pause()

            torrent.add_torrent(t, file)
            file.delete_after_greenlet()
        finally:
            try:
                t.remove()
            except KeyError:
                pass
            except RuntimeError:
                pass
    finally:
        torrentengine.session.unregister_alert('metadata_received_alert', on_metadata_received_alert)
        torrent.config.queue.active_downloads -= 1
