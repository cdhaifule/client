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
import gevent
import chardet
import libtorrent as lt

from urllib import unquote
from gevent import Timeout
from gevent.lock import RLock
from gevent.event import Event, AsyncResult

from . import logger
from .config import globalconfig

log = logger.get('torrentengine')

config = globalconfig.new('torrent')
config.default('compact_allocation', False, bool)
config.default('resolve_countries', False, bool)


############### tool functions

def decode_string(s, encoding="utf8"):
    if not s:
        return u''
    elif isinstance(s, unicode):
        return s

    encodings = [lambda: ("utf8", 'strict'),
                 lambda: ("iso-8859-1", 'strict'),
                 lambda: (chardet.detect(s)["encoding"], 'strict'),
                 lambda: (encoding, 'ignore')]

    if not encoding is "utf8":
        encodings.insert(0, lambda: (encoding, 'strict'))

    for l in encodings:
        try:
            return s.decode(*l())
        except UnicodeDecodeError:
            pass
    return u''

def utf8_encoded(s, encoding="utf8"):
    if isinstance(s, str):
        s = decode_string(s, encoding).encode("utf8")
    elif isinstance(s, unicode):
        s = s.encode("utf8")
    return s

def sanitize_filepath(filepath, folder=False):
    """
    Returns a sanitized filepath to pass to libotorrent rename_file().
    The filepath will have backslashes substituted along with whitespace
    padding and duplicate slashes stripped. If `folder` is True a trailing
    slash is appended to the returned filepath.
    """
    def clean_filename(filename):
        filename = filename.strip()
        if filename.replace('.', '') == '':
            return ''
        return filename

    if '\\' in filepath or '/' in filepath:
        folderpath = filepath.replace('\\', '/').split('/')
        folderpath = [clean_filename(x) for x in folderpath]
        newfilepath = '/'.join(filter(None, folderpath))
    else:
        newfilepath = clean_filename(filepath)

    if folder is True:
        return newfilepath + '/'
    else:
        return newfilepath


############### session

session = None

class Session(object):
    def __init__(self, torrent_dir, session_state_file=None, dht_state_file=None):
        global session
        if session is not None:
            raise RuntimeError('only one torrent session is allowed')

        session = self
        
        self._started = False

        self.torrents = dict()
        self.lock = RLock()

        self.torrent_dir = torrent_dir
        self.session_state_file = session_state_file
        self.dht_state_file = dht_state_file

        self.session = None
        self.auto_shutdown = True
        self.alert_handlers = dict()

        self.startup_funcs = list()
        self.shutdown_funcs = list()

        self.proxy = None

        self.use_dht = True
        self.use_upnp = True
        self.use_natpmp = True
        self.use_lsd = True

    # callback functions

    def add_startup_func(self, func):
        self.startup_funcs.append(func)

    def remove_startup_func(self, func):
        self.startup_funcs.remove(func)

    def add_shutdown_func(self, func):
        self.shutdown_funcs.append(func)

    def remove_shutdown_func(self, func):
        self.shutdown_funcs.remove(func)

    # start/stop functions

    def startup(self):
        if self.is_running:
            return

        log.info('starting torrent engine')

        if self.session is None:
            self.session = lt.session()
        self.load_state()

        self._started = True

        # register alerts
        self.session.set_alert_mask(
            lt.alert.category_t.error_notification |
            lt.alert.category_t.port_mapping_notification |
            lt.alert.category_t.storage_notification |
            lt.alert.category_t.tracker_notification |
            lt.alert.category_t.status_notification |
            lt.alert.category_t.ip_block_notification |
            lt.alert.category_t.performance_warning)

        # setup services
        self.update_dht()
        self.update_upnp()
        self.update_natpmp()
        self.update_lsd()

        # setup proxy
        self.update_proxy()

        # call startup callbacks
        for func in self.startup_funcs:
            func()

        # resume our session and start alert handler
        self.session.resume()
        gevent.spawn_later(0.1, self.handle_alerts)

    def shutdown(self):
        if not self.is_running:
            return
        
        log.info('stopping torrent engine')

        # stop services
        self.session.stop_dht()
        self.session.stop_upnp()
        self.session.stop_natpmp()
        self.session.stop_lsd()

        # call shutdown callbacks
        for func in self.shutdown_funcs:
            func()

        # stop our session
        #self.session.stop()
        #self.session.shutdown()
        # don't delete our session since libtorrent has no terminate functionallity
        #self.session = None
        self._started = False

    @property
    def is_running(self):
        return self.session is not None and self._started

    # session settings

    def get_settings(self):
        return self.session.get_settings()

    def set_settings(self, settings):
        self.session.set_settings(settings)

    def listen_on(self, a, b):
        self.session.listen_on(a, b)

    def set_max_uploads(self, a):
        self.session.set_max_uploads(a)

    def set_max_connections(self, a):
        self.session.set_max_connections(a)

    def set_upload_rate_limit(self, a):
        self.session.set_upload_rate_limit(a)

    def set_download_rate_limit(self, a):
        self.session.set_download_rate_limit(a)

    # dht functions

    def set_dht(self, use):
        self.use_dht = use
        self.update_dht()

    def update_dht(self):
        if not self.is_running:
            return
        if self.use_dht:
            try:
                state = self.load_dht_state()
                self.session.start_dht(state)
                gevent.spawn_later(1, self.handle_dht_state)
            except Exception, e:
                log.warning("Restoring old DHT state failed: %s", e)

            # setup dht routers
            self.session.add_dht_router("router.bittorrent.com", 6881)
            self.session.add_dht_router("router.utorrent.com", 6881)
            self.session.add_dht_router("router.bitcomet.com", 6881)
        else:
            self.session.stop_dht()

    def load_dht_state(self):
        if self.dht_state_file is None:
            return
        try:
            with open(self.dht_state_file, "rb") as f:
                return lt.bdecode(f.read())
        except Exception, e:
            #log.warning("Unable to read DHT state file: %s", e)
            return None

    def handle_dht_state(self):
        if not self.is_running or self.dht_state_file is None or not self.use_dht:
            return
        try:
            with open(self.dht_state_file, "wb") as f:
                f.write(lt.bencode(self.session.dht_state()))
        finally:
            gevent.spawn_later(1, self.handle_dht_state)

    # upnp functions

    def set_upnp(self, use):
        self.use_upnp = use
        self.update_upnp()

    def update_upnp(self):
        if not self.is_running:
            return
        if self.use_upnp:
            self.session.start_upnp()
        else:
            self.session.stop_upnp()

    # natpmp functions

    def set_natpmp(self, use):
        self.use_natpmp = use
        self.update_natpmp()

    def update_natpmp(self):
        if not self.is_running:
            return
        if self.use_natpmp:
            self.session.start_natpmp()
        else:
            self.session.stop_natpmp()

    # lsd functions

    def set_lsd(self, use):
        self.use_lsd = use
        self.update_lsd()

    def update_lsd(self):
        if not self.is_running:
            return
        if self.use_lsd:
            self.session.start_lsd()
        else:
            self.session.stop_lsd()

    # session state functions

    def save_state(self):
        if self.session_state_file is not None:
            data = lt.bencode(self.session.save_state())
            with open(self.session_state_file, 'wb') as f:
                f.write(data)

    def load_state(self):
        if self.session_state_file is not None:
            try:
                with open(self.session_state_file, 'rb') as f:
                    data = f.read()
                self.session.load_state(data)
            except BaseException as e:
                log.warning('error loading session state file: {}'.format(e))

    # alert functions

    def handle_alerts(self):
        if not self.is_running:
            return
        try:
            alerts = self.session.pop_alerts()
            for alert in alerts:
                alert_type = type(alert).__name__
                
                message = decode_string(alert.message())
                #if alert_type not in ('portmap_log_alert', 'dht_reply_alert', 'state_update_alert', 'dht_reply_alert'):
                #if alert_type not in ('portmap_log_alert', 'dht_reply_alert', 'dht_reply_alert'):
                #    try:
                #        log.debug(u'alert: {}: {}'.format(alert_type, message))
                #    except UnicodeEncodeError:
                #        log.error('error decoding unicode...')

                if alert_type in self.alert_handlers:
                    for func in self.alert_handlers[alert_type]:
                        func(alert)
        finally:
            gevent.spawn_later(0.1, self.handle_alerts)

    def register_alert(self, alert, func):
        if alert not in self.alert_handlers:
            self.alert_handlers[alert] = list()
        self.alert_handlers[alert].append(func)

    def unregister_alert(self, alert, func):
        try:
            self.alert_handlers[alert].remove(func)
        except KeyError:
            pass
        except ValueError:
            pass

    # delete resume and torrent files (needed globally)

    def delete_torrent(self, id):
        path = os.path.join(session.torrent_dir, id+'.torrent')
        try:
            os.unlink(path)
        except:
            pass

    def delete_resume_data(self, id):
        path = os.path.join(self.torrent_dir, id+'.resume_data')
        for p in [path, path+'.tmp']:
            try:
                os.unlink(p)
            except:
                pass

    # add/remove functions

    def add_torrent(self, options):
        with self.lock:
            self.startup()
            return self.session.add_torrent(options)

    def add_magnet(self, magnet, options):
        with self.lock:
            self.startup()
            return lt.add_magnet_uri(self.session, utf8_encoded(magnet), options)

    def remove_torrent(self, handle):
        with self.lock:
            if not self.is_running:
                return
            self.session.remove_torrent(handle)
            if self.auto_shutdown and not self.session.get_torrents() and not self.torrents:
                self.shutdown()

    # proxy functions

    def set_proxy(self, host, port, username, password, type):
        self.proxy = (host, port, username, password, type)
        self.update_proxy()

    def remove_proxy(self):
        self.proxy = None
        self.update_proxy()

    def update_proxy(self):
        if not self.is_running:
            return
        data = self.session.proxy()
        if self.proxy is None:
            data.hostname = ""
            data.port = 0
            data.username = ""
            data.password = ""
            data.type = lt.proxy_type.none
            data.proxy_hostnames = True
            data.proxy_peer_connections = True
        else:
            host, port, username, password, type = self.proxy
            data.hostname = str(host)
            data.port = port
            data.username = username and str(username) or ""
            data.password = password and str(password) or ""
            if type in ('http', 'https'):
                data.type = password and lt.proxy_type.http_pw or lt.proxy_type.http
            elif type == 'socks5':
                data.type = password and lt.proxy_type.socks5_pw or lt.proxy_type.socks5
                print "socks5", lt.proxy_type.socks5
            elif type == 'socks4':
                data.type = lt.proxy_type.socks4
            else:
                data.type = lt.proxy_type.none
                raise ValueError('proxy type {} not supported!'.format(type))
            data.proxy_hostnames = True
            data.proxy_peer_connections = True
        self.session.set_proxy(data)
        #self.session.set_i2p_proxy(data)


############### torrent

class Torrent(object):
    def __init__(self, data=None, magnet=None, options=None):
        self.options = options or dict()

        self.alerts = dict()

        self._id_event = Event()
        self.save_lock = RLock()
        self.edit_lock = RLock()

        if config.compact_allocation:
            self.options['storage_mode'] = lt.storage_mode_t(2)
        else:
            self.options['storage_mode'] = lt.storage_mode_t(1)

        self.options["paused"] = True
        self.options["auto_managed"] = False
        self.options["duplicate_is_error"] = True

        if data or magnet:
            self._init(data, magnet)

    def _init(self, data, magnet):
        self.magnet = magnet

        if data is not None:
            self._info = lt.torrent_info(lt.bdecode(data))
            self.options['ti'] = self._info
            self.id = str(self._info.info_hash())
            self._id_event.set()

            resume_data = self.load_resume_data()
            if resume_data:
                self.options["resume_data"] = resume_data

            self.handle = session.add_torrent(self.options)
        else:
            def _on_metadata_received_alert(alert):
                self._info = self.handle.get_torrent_info()
                self.options['ti'] = self.info
                self.unregister_alert('metadata_received_alert', _on_metadata_received_alert)

            self.register_alert('metadata_received_alert', _on_metadata_received_alert)

            self._info = None
            self.handle = session.add_magnet(magnet, self.options)
            self.id = str(self.handle.info_hash())
            self._id_event.set()

        if self.id == '0000000000000000000000000000000000000000':
            self.remove()
            raise ValueError('error getting torrent id. torrent broken?')

        session.torrents[self.id] = self

    def register_alert(self, alert, func):
        def _(alert):
            self._id_event.wait()
            if str(alert.handle.info_hash()) == self.id:
                func(alert)

        if alert not in self.alerts:
            self.alerts[alert] = list()
        self.alerts[alert].append((func, _))
        session.register_alert(alert, _)

    def unregister_alert(self, alert, func):
        try:
            for a in self.alerts[alert]:
                if a[0] == func:
                    self.alerts[alert].remove(a)
                    session.unregister_alert(alert, a[1])
            if not self.alerts[alert]:
                del self.alerts[alert]
        except KeyError:
            pass

    @property
    def info(self):
        if self._info is None:
            if self.has_metadata:
                self._info = self.handle.get_torrent_info()
        return self._info

    @property
    def name(self):
        if self.has_metadata:
            name = self.info.name()
            return decode_string(name)
        elif self.magnet:
            try:
                keys = dict([k.split('=') for k in self.magnet.split('?')[-1].split('&')])
                name = keys.get('dn')
                if name:
                    name = unquote(name).replace('+', ' ')
                    return decode_string(name)
            except:
                pass

    @property
    def has_metadata(self):
        return self.handle.has_metadata()

    @property
    def metadata(self):
        return self.info.metadata()

    def wait_metadata(self, timeout=None):
        if self.has_metadata:
            return

        e = Event()

        def on_metadata_received_alert(alert):
            e.set()

        self.register_alert('metadata_received_alert', on_metadata_received_alert)
        try:
            e.wait(timeout)
        finally:
            self.unregister_alert('metadata_received_alert', on_metadata_received_alert)

    def save_torrent(self):
        with self.save_lock:
            path = os.path.join(session.torrent_dir, self.id+'.torrent')
            data = dict(info=lt.bdecode(self.metadata))
            with open(path, "wb") as f:
                f.write(lt.bencode(data))

    def delete_torrent(self):
        with self.save_lock:
            session.delete_torrent(self.id)

    # status
    @property
    def status(self):
        return self.handle.status()

    # other properties

    @property
    def files(self):
        """Returns a list of files this torrent contains"""
        if not self.has_metadata:
            return []
        ret = []
        files = self.info.files()
        for index, file in enumerate(files):
            ret.append({
                'index': index,
                'path': file.path.decode("utf8").replace('\\', '/'),
                'size': file.size,
                'offset': file.offset})
        return ret

    @property
    def peers(self):
        """Returns a list of peers and various information about them"""
        ret = []
        peers = self.handle.get_peer_info()

        for peer in peers:
            # We do not want to report peers that are half-connected
            if peer.flags & peer.connecting or peer.flags & peer.handshake:
                continue

            client = decode_string(str(peer.client))
            # Make country a proper string
            country = str()
            for c in peer.country:
                if not c.isalpha():
                    country += " "
                else:
                    country += c

            ret.append({
                "client": client,
                "country": country,
                "down_speed": peer.payload_down_speed,
                "ip": "%s:%s" % (peer.ip[0], peer.ip[1]),
                "progress": peer.progress,
                "seed": peer.flags & peer.seed,
                "up_speed": peer.payload_up_speed})

        return ret

    @property
    def file_progress(self):
        """Returns the file progress as a list of floats.. 0.0 -> 1.0"""
        if not self.has_metadata:
            return

        file_progress = self.handle.file_progress()
        ret = []
        for i, f in enumerate(self.files):
            try:
                ret.append(float(file_progress[i]) / float(f["size"]))
            except ZeroDivisionError:
                ret.append(0.0)

        return ret

    @property
    def pieces(self):
        if not self.has_metadata:
            return None

        pieces = dict()
        # First get the pieces availability.
        availability = self.handle.piece_availability()
        # Pieces from connected peers
        for peer_info in self.handle.get_peer_info():
            if peer_info.downloading_piece_index < 0:
                # No piece index, then we're not downloading anything from
                # this peer
                continue
            pieces[peer_info.downloading_piece_index] = 2

        # Now, the rest of the pieces
        for idx, piece in enumerate(self.status.pieces):
            if idx in pieces:
                # Piece beeing downloaded, handled above
                continue
            elif piece:
                # Completed Piece
                pieces[idx] = 3
                continue
            elif availability[idx] > 0:
                # Piece not downloaded nor beeing downloaded but available
                pieces[idx] = 1
                continue
            # If we reached here, it means the piece is missing, ie, there's
            # no known peer with this piece, or this piece has not been asked
            # for so far.
            pieces[idx] = 0

        sorted_indexes = pieces.keys()
        sorted_indexes.sort()
        # Return only the piece states, no need for the piece index
        # Keep the order
        return [pieces[idx] for idx in sorted_indexes]

    def pause(self):
        self.auto_managed(False)
        self.handle.pause()
    
    def resume(self):
        self.auto_managed(False)
        self.handle.resume()

    def pause_automanaged(self):
        self.auto_managed(False)
        self.handle.pause()

    def resume_automanaged(self):
        self.auto_managed(True)
        self.handle.pause()

    def auto_managed(self, auto_managed):
        self.handle.auto_managed(auto_managed)

    def force_reannounce(self):
        self.handle.force_reannounce()

    def is_finished(self):
        return self.handle.is_finished()

    def remove(self):
        """removes torrent from session
        """
        with self.save_lock, self.edit_lock:
            for alert, a in dict(self.alerts).iteritems():
                for b in a:
                    self.unregister_alert(alert, b[0])

            self.pause()
            try:
                del session.torrents[self.id]
            except KeyError:
                pass
            session.remove_torrent(self.handle)

    def delete(self):
        """removes torrent from session and deletes all session files from hdd
        """
        self.remove()
        self.delete_torrent()
        self.delete_resume_data()

    # fast resume
    
    def save_resume_data(self, async=False):
        with self.save_lock:
            if not self.status.need_save_resume:
                return

            event = Event()

            def _on_alert_save_resume_data(alert):
                try:
                    path = os.path.join(session.torrent_dir, self.id+'.resume_data')
                    tmp = path+'.tmp'
                    with open(tmp, "wb") as f:
                        f.write(lt.bencode(alert.resume_data))
                        os.fsync(f)
                    try:
                        os.unlink(path)
                    except:
                        pass
                    os.rename(tmp, path)
                except IOError, e:
                    log.debug("Unable to save .resume_data: %s", e)
                finally:
                    _remove_alerts()

            def _on_alert_save_resume_data_failed(alert):
                log.warning('error saving resume_data: {}'.format(alert))
                _remove_alerts()

            def _remove_alerts():
                self.unregister_alert('save_resume_data_alert', _on_alert_save_resume_data)
                self.unregister_alert('save_resume_data_failed_alert', _on_alert_save_resume_data_failed)
                event.set()

            self.register_alert('save_resume_data_alert', _on_alert_save_resume_data)
            self.register_alert('save_resume_data_failed_alert', _on_alert_save_resume_data_failed)

            print "saving resume data"
            self.handle.save_resume_data()
            print "saving resume data done"

            if not async:
                event.wait()

    def load_resume_data(self):
        with self.save_lock:
            try:
                path = os.path.join(session.torrent_dir, self.id+'.resume_data')
                with open(path, "rb") as f:
                    return f.read()
            except IOError, e:
                log.debug("Unable to load .resume_data: %s", e)

    def delete_resume_data(self):
        with self.save_lock:
            session.delete_resume_data(self.id)

    def rename_files(self, filenames):
        """Renames files in the torrent. 'filenames' should be a list of
        (index, filename) pairs."""
        with self.edit_lock:
            pending = dict()
            result = AsyncResult()

            def _on_file_renamed_alert(alert):
                if alert.index in pending:
                    del pending[alert.index]
                if not pending:
                    result.set(None)

            def _on_file_rename_failed_alert(alert):
                if alert.index in pending:
                    del pending[alert.index]
                del pending[alert.index]
                result.exception(alert)

            files = self.files
            self.register_alert('file_renamed_alert', _on_file_renamed_alert)
            self.register_alert('file_rename_failed_alert', _on_file_rename_failed_alert)
            try:
                for index, filename in filenames:
                    # Make sure filename is a unicode object
                    try:
                        filename = unicode(filename, "utf-8")
                    except TypeError:
                        pass
                    filename = sanitize_filepath(filename)
                    # libtorrent needs unicode object if wstrings are enabled, utf8 bytestring otherwise

                    try:
                        if files[index]['path'] == filename:
                            continue
                    except TypeError:
                        pass
                    try:
                        if files[index]['path'] == filename.encode('utf-8'):
                            continue
                    except TypeError:
                        pass

                    try:
                        pending[index] = filename
                        self.handle.rename_file(index, pending[index])
                        print "rename 1", index, pending[index]
                    except TypeError:
                        pending[index] = filename.encode('utf-8')
                        self.handle.rename_file(index, pending[index])
                        print "rename 2", index, pending[index]
                
                if pending:
                    print "!"*100, "rename"
                    print pending
                    i = 0
                    while True:
                        try:
                            result.get(timeout=1)
                            break
                        except Timeout:
                            files = self.files
                            for index, filename in pending.items():
                                if files[index]['path'] == filename:
                                    del pending[index]
                            if not pending:
                                break
                            print "still pending", pending
                            i += 1
                            if i > 60:
                                raise
                    print "!"*100, "rename done"
            finally:
                self.unregister_alert('file_renamed_alert', _on_file_renamed_alert)
                self.unregister_alert('file_rename_failed_alert', _on_file_rename_failed_alert)

    def move_storage(self, dest):
        with self.edit_lock:
            try:
                dest = unicode(dest, "utf-8")
            except TypeError:
                # String is already unicode
                pass

            if not os.path.exists(dest):
                try:
                    # Try to make the destination path if it doesn't exist
                    os.makedirs(dest)
                except IOError, e:
                    log.exception(e)
                    log.error("Could not move storage for torrent %s since %s does "
                              "not exist and could not create the directory.",
                              self.id, dest)
                    raise

            result = AsyncResult()

            def _on_storage_moved_alert(alert):
                result.set(None)

            def _on_storage_moved_failed_alert(alert):
                result.exception(alert)

            self.register_alert('storage_moved_alert', _on_storage_moved_alert)
            self.register_alert('storage_moved_failed_alert', _on_storage_moved_failed_alert)
            try:
                # libtorrent needs unicode object if wstrings are enabled, utf8 bytestring otherwise
                try:
                    self.handle.move_storage(dest)
                except TypeError:
                    self.handle.move_storage(dest.encode('utf-8'))
                result.get(timeout=300)
                self.save_resume_data()
            finally:
                self.unregister_alert('storage_moved_alert', _on_storage_moved_alert)
                self.unregister_alert('storage_moved_failed_alert', _on_storage_moved_failed_alert)
