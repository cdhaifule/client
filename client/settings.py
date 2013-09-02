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
import sys
import uuid
import platform

module_dir = os.path.dirname(__file__)
script_dir = os.path.abspath(os.path.dirname(module_dir))

filesystem_encoding = sys.getfilesystemencoding()

if sys.platform == "darwin":
    home_dir = os.environ["HOME"].decode(filesystem_encoding)
    data_dir = os.path.join(home_dir, "Library", 'download.am')
elif sys.platform == "win32":
    if platform.release() == 'XP':
        os.environ['LOCALAPPDATA'] = os.environ['APPDATA']
    home_dir = os.path.join(os.environ["HOMEDRIVE"], os.environ["HOMEPATH"].decode(filesystem_encoding))
    data_dir = os.path.join(os.environ["LOCALAPPDATA"].decode(filesystem_encoding), 'download.am-data')
else:
    home_dir = os.environ["HOME"].decode(filesystem_encoding)
    data_dir = os.path.join(home_dir, ".download.am")

app_uuid_file = os.path.join(data_dir, '.app.uuid')
next_uid_file = os.path.join(data_dir, '.next.id')    # when integer no file is used. useful for debug

db_file = os.path.join(data_dir, 'dlam.db')
config_file = os.path.join(data_dir, 'config.json')

log_file = os.path.join(data_dir, 'dlam.log')
log_settings_file = os.path.join(data_dir, 'log.json')

localize_file = os.path.join(data_dir, '.localize')

temp_dir = os.path.join(data_dir, 'tmp')

torrent_dir = os.path.join(data_dir, 'torrents')
torrent_session_state_file = os.path.join(data_dir, 'torrent.state')
torrent_dht_state_file = os.path.join(data_dir, 'torrent-dht.state')

external_plugins = os.path.join(data_dir, "extern")


frontend_domain = "www.download.am"
patchserver = "http://repo.download.am"

app_build = 1
app_uuid = None
keyring_service = None
use_keyring = True

if "nose" in sys.argv[0] and use_keyring:
    import keyring
    from keyring.backend import KeyringBackend

    class DummyKeyring(KeyringBackend):
        def __init__(self):
            self.keys = dict()

        def supported(self):
            return True

        def get_password(self, service, username):
            return self.keys.get(username)

        def set_password(self, service, username, password):
            self.keys[username] = password

        def delete_password(self, service, username):
            del self.keys[username]
            
    keyring.set_keyring(DummyKeyring())

try:
    sys.frozen
except AttributeError:
    sys.frozen = False

if sys.platform == "win32" and sys.frozen:
    app_dir = os.path.dirname(sys.executable)
    img_dir = os.path.join(app_dir, 'img')
    mainicon = os.path.join(img_dir, 'dlam.ico')
    taskbaricon = mainicon
    taskbaricon_inactive = os.path.join(img_dir, 'dlam_grey.ico')
    bin_dir = os.path.join(app_dir, "bin")
    menuiconfolder = os.path.join(app_dir, 'img', 'menu')
    import requests
    requests.certs.where = lambda: os.environ["REQUESTS_CA_BUNDLE"]
    os.environ['SSL_CERT_FILE'] = os.path.join(bin_dir, "cacert.pem")
    os.environ["REQUESTS_CA_BUNDLE"] = os.environ['SSL_CERT_FILE']
elif sys.platform == "darwin" and sys.frozen:
    app_dir = sys.executable.split(".app/Contents/", 1)[0] + ".app"
    bin_dir = os.path.join(app_dir, "Contents", "Resources", "bin")
    img_dir = os.path.join(app_dir, 'Contents/Resources/lib/python2.7/client/img')
    mainicon = os.path.join(img_dir, "dlam.icns")
    taskbaricon = os.path.join(img_dir, "dlam_black.icns")
    taskbaricon_inactive = os.path.join(img_dir, "dlam_greyx32.png")
    menuiconfolder = os.path.join(img_dir, "menu")
    os.environ['SSL_CERT_FILE'] = os.path.join(bin_dir, "cacert.pem")
    os.environ["REQUESTS_CA_BUNDLE"] = os.environ['SSL_CERT_FILE']
else:
    app_dir = os.getcwd()
    bin_dir = os.path.join(app_dir, "bin")
    menuiconfolder = os.path.join(app_dir, "client/img/menu")
    img_dir = os.path.join(app_dir, 'client/img')
    if sys.platform == "darwin":
        os.environ['SSL_CERT_FILE'] = os.path.join(bin_dir, "cacert.pem")
        mainicon = os.path.join(app_dir, "client/img/dlam.icns")
        taskbaricon = os.path.join(img_dir, "dlam_black.icns")
        taskbaricon_inactive = os.path.join(img_dir, "dlam_greyx32.png")
    else:
        mainicon = os.path.join(img_dir, "dlam.ico")
        taskbaricon = mainicon
        taskbaricon_inactive = taskbaricon

def _makedirs(path):
    try:
        try:
            os.makedirs(path)
            return
        except OSError as e:
            if e.errno != 17:
                raise
    except BaseException as e:
        print >>sys.stderr, 'error creating file or directory {}: {}'.format(path, e)
        sys.exit(1)

_makedirs(data_dir)
_makedirs(torrent_dir)
_makedirs(temp_dir)
_makedirs(external_plugins)

def init_pre():
    global log
    global app_uuid
    global keyring_service

    from . import logger
    log = logger.get("settings")

    os.chdir(script_dir)
    try:
        with open(app_uuid_file, 'r') as f:
            app_uuid = f.read().strip()
    except:
        app_uuid = None

    if not app_uuid:
        app_uuid = str(uuid.uuid4())
        while len(app_uuid) < 36:
            app_uuid += app_uuid
        app_uuid = app_uuid[:36]
        with open(app_uuid_file, 'wb') as f:
            f.write(app_uuid)
        log.info('created new app uuid: {}'.format(app_uuid))

    keyring_service = "download.am_client_" + app_uuid

def init():
    pass
