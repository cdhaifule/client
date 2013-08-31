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
import time
import gevent
import urllib
import libtorrent as lt

from gevent.lock import Semaphore
from gevent.pool import Group

from . import event, core, proxy, logger, interface, settings
from .torrentengine import Session, Torrent
from .plugintools import FakeGreenlet
from .scheme import transaction, Column
from .config import globalconfig

cache = dict()
torrents = dict()

log = logger.get('torrent')

session = Session(settings.torrent_dir, dht_state_file=settings.torrent_dht_state_file)


# config

config = globalconfig.new('torrent')
config.default('state', 'started', str)  # started|paused|stopped


# config.limit

def config_limit_changed():
    if session.is_running:
        session.set_max_uploads(config.limit.max_uploads)
        session.set_max_connections(config.limit.max_connections)

        if config.limit.upload_rate is None:
            session.set_upload_rate_limit(1)
        elif config.limit.upload_rate == 0:
            session.set_upload_rate_limit(-1)
        else:
            session.set_upload_rate_limit(config.limit.upload_rate)
        
        if config.limit.download_rate is None:
            session.set_download_rate_limit(1)
        elif config.limit.download_rate == 0:
            session.set_download_rate_limit(-1)
        else:
            session.set_download_rate_limit(config.limit.download_rate)

        settings = session.get_settings()
        settings['half_open_limit'] = config.limit.half_open
        settings['ignore_limits_on_local_network'] = config.limit.ignore_limits_on_local_network
        session.set_settings(settings)
session.add_startup_func(config_limit_changed)

config.limit.default('max_connections', 200, int, hook=config_limit_changed, description='Maximal number of connections')
config.limit.default('max_uploads', 4, int, hook=config_limit_changed, description='Maximal number of upload slots')

config.limit.default('upload_rate', 0, int, hook=config_limit_changed, allow_none=True, description='Maximal upload rate')       # when set to null upload is disabled
config.limit.default('download_rate', 0, int, hook=config_limit_changed, allow_none=True, description='Maximal download rate')   # when set to null download is disabled

config.limit.default('half_open', 50, int, hook=config_limit_changed, description='Maximal half open connections')
config.limit.default('ignore_limits_on_local_network', True, bool, hook=config_limit_changed, description='Ignore limits on local networks')


# config.network

@config.network.register('listen_ports')
def config_network_listen_ports_changed():
    if session.is_running:
        session.listen_on(*config.network.listen_ports)
session.add_startup_func(config_network_listen_ports_changed)

config.network.default('listen_ports', [6881, 6891], list, hook=config_network_listen_ports_changed)
config.network.default('use_dht', True, bool, hook=session.set_dht)
config.network.default('use_upnp', True, bool, hook=session.set_upnp)
config.network.default('use_natpmp', True, bool, hook=session.set_natpmp)
config.network.default('use_lsd', True, bool, hook=session.set_lsd, description='Local Service Discovery')
# TODO: encryption (pe_settings)
# TODO: network interface
# TODO: outgoing ports


# config.cache

def config_cache_changed():
    if session.is_running:
        settings = session.get_settings()
        settings['cache_size'] = config.cache.size
        settings['expiry'] = config.cache.expiry
        session.set_settings(settings)
session.add_startup_func(config_cache_changed)

config.cache.default('size', 1024, int, hook=config_cache_changed, description='Cache size (16 KiB blocks)')
config.cache.default('expiry', 120, int, hook=config_cache_changed, description='Cache livetime')

#default_settings['default_cache_min_age'] = 1
#default_settings['use_read_cache'] = True
#default_settings['read_cache_line_size'] = 32
#default_settings['explicit_read_cache'] = False
#default_settings['cache_buffer_chunk_size'] = 16
#default_settings['write_cache_line_size'] = 32
#default_settings['volatile_read_cache'] = False
#default_settings['disk_cache_algorithm'] = 2
#default_settings['lock_disk_cache'] = False
#default_settings['guided_read_cache'] = False


# config.queue

def config_queue_changed():
    if session.is_running:
        settings = session.get_settings()
        settings['active_limit'] = config.queue.active_limit
        settings['active_downloads'] = config.queue.active_downloads
        settings['active_seeds'] = config.queue.active_seeds
        settings['dont_count_slow_torrents'] = config.queue.dont_count_slow_torrents
        session.set_settings(settings)
session.add_startup_func(config_queue_changed)

config.queue.default('active_limit', 8, int, hook=config_queue_changed, description='Active total')
config.queue.default('active_downloads', 5, int, hook=config_queue_changed, description='Active downloads')
config.queue.default('active_seeds', 3, int, hook=config_queue_changed, description='Active seeds')
config.queue.default('dont_count_slow_torrents', True, bool, hook=config_queue_changed, description='Do not count slow torrents')
# TODO: seed upload ratio and time limits








# config


# settings

"""default_settings = dict()
default_settings['connections_limit'] = 20
default_settings['user_agent'] = 'libtorrent/{}-download.am'.format(lt.version)
default_settings['send_redundant_have'] = True
default_settings['min_announce_interval'] = 300
default_settings['anonymous_mode'] = False
default_settings['urlseed_wait_retry'] = 30
default_settings['auto_upload_slots_rate_based'] = True
default_settings['tracker_receive_timeout'] = 40
default_settings['inactivity_timeout'] = 600
default_settings['ignore_limits_on_local_network'] = True
default_settings['rate_limit_ip_overhead'] = True
default_settings['rate_limit_utp'] = True
default_settings['local_download_rate_limit'] = 0
default_settings['tracker_completion_timeout'] = 60
default_settings['request_timeout'] = 50
default_settings['close_redundant_connections'] = True
default_settings['auto_manage_interval'] = 30
default_settings['recv_socket_buffer_size'] = 0
default_settings['optimize_hashing_for_speed'] = True
default_settings['announce_to_all_tiers'] = False
default_settings['file_pool_size'] = 40
#default_settings['peer_turnover_cutoff'] = 0.8999999761581421
default_settings['send_buffer_watermark'] = 512000
default_settings['seeding_piece_quota'] = 20
default_settings['mixed_mode_algorithm'] = 1
default_settings['auto_manage_startup'] = 120
default_settings['lock_files'] = False
default_settings['listen_queue_size'] = 5
default_settings['max_queued_disk_bytes_low_watermark'] = 0
default_settings['suggest_mode'] = 0
default_settings['ssl_listen'] = 4433
default_settings['request_queue_time'] = 3
default_settings['urlseed_pipeline_size'] = 5
default_settings['incoming_starts_queued_torrents'] = False
default_settings['initial_picker_threshold'] = 4
default_settings['seed_time_limit'] = 86400
default_settings['handshake_timeout'] = 10
default_settings['free_torrent_hashes'] = True
default_settings['peer_tos'] = '\x00'
default_settings['use_disk_read_ahead'] = True
default_settings['unchoke_slots_limit'] = 20
default_settings['max_rejects'] = 50
default_settings['half_open_limit'] = 2147483647
default_settings['stop_tracker_timeout'] = 5
default_settings['utp_dynamic_sock_buf'] = False
default_settings['allow_i2p_mixed'] = False
default_settings['share_ratio_limit'] = 2.0
default_settings['active_lsd_limit'] = 60
default_settings['active_tracker_limit'] = 360
default_settings['broadcast_lsd'] = True
default_settings['send_buffer_low_watermark'] = 512
default_settings['max_paused_peerlist_size'] = 4000
default_settings['always_send_user_agent'] = False
default_settings['tracker_maximum_response_length'] = 1048576
default_settings['upnp_ignore_nonrouters'] = False
default_settings['no_recheck_incomplete_resume'] = False
default_settings['max_queued_disk_bytes'] = 1048576
default_settings['drop_skipped_requests'] = False
default_settings['max_metadata_size'] = 3145728
default_settings['auto_upload_slots'] = True
default_settings['utp_gain_factor'] = 1500
default_settings['peer_timeout'] = 120
default_settings['report_true_downloaded'] = False
default_settings['apply_ip_filter_to_trackers'] = True
default_settings['seed_time_ratio_limit'] = 7.0
default_settings['prefer_udp_trackers'] = True
default_settings['unchoke_interval'] = 15
default_settings['use_dht_as_fallback'] = False
default_settings['max_sparse_regions'] = 0
default_settings['connection_speed'] = 6
default_settings['num_optimistic_unchoke_slots'] = 0
default_settings['enable_outgoing_tcp'] = True
default_settings['tick_interval'] = 100
default_settings['max_failcount'] = 3
default_settings['local_upload_rate_limit'] = 0
default_settings['enable_outgoing_utp'] = True
default_settings['utp_num_resends'] = 6
default_settings['enable_incoming_tcp'] = True
default_settings['local_service_announce_interval'] = 300
default_settings['allow_reordered_disk_operations'] = True
default_settings['optimistic_unchoke_interval'] = 30
default_settings['use_parole_mode'] = True
default_settings['peer_connect_timeout'] = 15
default_settings['no_connect_privileged_ports'] = True
default_settings['alert_queue_size'] = 1000
default_settings['num_want'] = 200
default_settings['seed_choking_algorithm'] = 0
default_settings['optimistic_disk_retry'] = 600
default_settings['udp_tracker_token_expiry'] = 60
default_settings['choking_algorithm'] = 0
default_settings['max_suggest_pieces'] = 10
default_settings['min_reconnect_time'] = 60
default_settings['announce_double_nat'] = False
default_settings['max_pex_peers'] = 50
default_settings['prioritize_partial_pieces'] = False
default_settings['coalesce_reads'] = False
default_settings['tracker_backoff'] = 250
default_settings['dont_count_slow_torrents'] = True
default_settings['max_allowed_in_request_queue'] = 250
default_settings['torrent_connect_boost'] = 10
default_settings['active_dht_limit'] = 88
default_settings['strict_end_game_mode'] = True
default_settings['auto_scrape_min_interval'] = 300
default_settings['utp_syn_resends'] = 2
default_settings['low_prio_disk'] = True
default_settings['no_atime_storage'] = True
default_settings['utp_fin_resends'] = 2
default_settings['increase_est_reciprocation_rate'] = 20
default_settings['auto_scrape_interval'] = 1800
default_settings['ignore_resume_timestamps'] = False
default_settings['disable_hash_checks'] = False
default_settings['urlseed_timeout'] = 20
default_settings['allowed_fast_set_size'] = 10
default_settings['announce_to_all_trackers'] = False
default_settings['lazy_bitfields'] = True
default_settings['seeding_outgoing_connections'] = True
default_settings['allow_multiple_connections_per_ip'] = False
default_settings['read_job_every'] = 10
default_settings['enable_incoming_utp'] = True
default_settings['utp_delayed_ack'] = 0
default_settings['piece_timeout'] = 20
default_settings['default_est_reciprocation_rate'] = 16000
default_settings['utp_target_delay'] = 100
default_settings['max_out_request_queue'] = 200
default_settings['coalesce_writes'] = False
#default_settings['peer_turnover'] = 0.03999999910593033
default_settings['auto_manage_prefer_seeds'] = False
default_settings['max_peerlist_size'] = 4000
default_settings['dht_upload_rate_limit'] = 4000
default_settings['utp_connect_timeout'] = 3000
default_settings['disk_io_write_mode'] = 0
default_settings['announce_ip'] = ''
default_settings['strict_super_seeding'] = False
default_settings['smooth_connects'] = True
default_settings['whole_pieces_threshold'] = 20
default_settings['disk_io_read_mode'] = 0
default_settings['file_checks_delay_per_block'] = 0
default_settings['decrease_est_reciprocation_rate'] = 3
default_settings['send_socket_buffer_size'] = 0"""

"""for k, v in default_settings.iteritems():
    # TODO: make variables type save
    config.settings.default(k, v, type(v))

def config_settings_changed():
    if session.is_running:
        settings = session.get_settings()
        for k in default_settings.keys():
            settings[k] = config.settings[k]
            if type(settings[k]) == unicode:
                settings[k] = str(settings[k])
        session.set_settings(settings)
session.add_startup_func(config.settings_changed)

for k in default_settings.keys():
    config.settings.register_hook(k, config.settings_changed)"""


# engine start/pause/stop

@config.register('state')
def on_config_state_changed():
    if session:
        for t in torrents.values():
            t.update_engine_state()

def torrent_session_startup():
    if session.is_running:
        settings = session.get_settings()
        settings['user_agent'] = 'libtorrent/{}-download.am'.format(lt.version)
        session.set_settings(settings)
session.add_startup_func(torrent_session_startup)


# tool functions

def convert_torrent_name(torrent_name, file_name):
    root = file_name
    base = None
    while True:
        r, sub = os.path.split(root)
        if not r:
            break
        root = r
        if not base:
            base = sub
        else:
            base = os.path.join(sub, base)
    if base and root == torrent_name:
        return base
    return file_name


# events

@event.register('proxy:changed')
def proxy_changed(*args):
    if proxy.config.enabled and not proxy.config.last_error:
        session.set_proxy(proxy.config.host, proxy.config.port, proxy.config.username, proxy.config.password, proxy.config.type)
    else:
        session.remove_proxy()
session.add_startup_func(proxy_changed)

@event.register('package:deleted')
def package_deleted(e, package):
    if package.system == 'torrent':
        try:
            if package.id in torrents:
                t = torrents[package.id]
                t.delete()
            elif package.id in session.torrents:
                session[package.id].delete()
            else:
                session.delete_torrent(package.id)
                session.delete_resume_data(package.id)
        except BaseException as e:
            raise
            log.error(e)

@event.register('packages:positions_updated')
def on_package_positions_updated(*args):
    for p in core.packages():
        if p.system == 'torrent' and p.id in torrents:
            torrents[p.id].handle.queue_position_bottom()

@event.register('package:reset')
def on_package_reset(e, package):
    if package.system != 'torrent':
        return
    if package.id in torrents:
        t = torrents[package.id]
        t.remove()
        t.delete_resume_data()
        TorrentJob(package)
    else:
        try:
            path = os.path.join(session.torrent_dir, package.id+'.resume_data')
            os.unlink(path)
        except:
            pass

@event.register('package.state:changed')
@event.register('package.enabled:changed')
def on_package_state_changed(e, package, old):
    if package.system != 'torrent':
        return
    if not package.enabled:
        return
    if package.state == 'download':
        if package.id not in torrents:
            TorrentJob(package)
    elif package.id in torrents:
        torrents[package.id].remove()

@event.register('package.enabled:changed')
def on_package_enabled_changed(e, package, old):
    if package.system != 'torrent':
        return
    if package.enabled and package.state == 'download':
        if package.id in torrents:
            torrents[package.id].update_engine_state()
    elif package.id in torrents:
        torrents[package.id].remove()

@event.register('file.enabled:changed')
def on_file_enabled_changed(e, file, old):
    if file.package.system != 'torrent':
        return
    if file.package.id not in torrents:
        return
    i = int(file.split_url.fragment)
    torrents[file.package.id].handle.file_priority(i, file.enabled and 1 or 0)
    if file.enabled and not file.working:
        if file.state == 'download':
            with transaction:
                file.greenlet = FakeGreenlet(file)

@event.register('file:deleted')
def on_file_deleted(e, file):
    if file.package.system != 'torrent':
        return
    if file.package.id not in torrents:
        return
    i = int(file.split_url.fragment)
    torrents[file.package.id].handle.file_priority(i, 0)


"""@event.register('package.name:changed')
def on_package_name_changed(e, package, old):
    if package.system != 'torrent' or package.state != 'download':
        return
    if package.id in torrents:
        torrents[package.id].update_storage()"""


# state monitor

monitor_greenlet = None

def monitor_start():
    global monitor_greenlet
    print "!"*100, 'monitor start'
    if monitor_greenlet is None:
        monitor_greenlet = gevent.spawn(monitor_func)

def monitor_stop():
    global monitor_greenlet
    print "!"*100, 'monitor stop'
    if monitor_greenlet is not None:
        monitor_greenlet.kill()
        monitor_greenlet = None

def monitor_func():
    global monitor_greenlet
    session.session.post_torrent_updates()
    monitor_greenlet = gevent.spawn_later(5, monitor_func)

def on_state_update_alert(alert):
    global monitor_greenlet
    with transaction:
        ids = set([])
        for s in alert.status:
            torrent_id = str(s.info_hash)
            if torrent_id not in torrents:
                continue
            torrents[torrent_id].update_status(s)
            ids.add(torrent_id)
        # check out all other torrents that got no status update. we need this at least for the speed miminizer
        for torrent_id in torrents:
            if torrent_id not in ids and torrents[torrent_id].state == 'download':
                torrents[torrent_id].update_file_progress()
    if monitor_greenlet:
        monitor_greenlet.kill()
        monitor_greenlet = gevent.spawn_later(1, monitor_func)

session.add_startup_func(monitor_start)
session.add_shutdown_func(monitor_stop)
session.register_alert('state_update_alert', on_state_update_alert)


# torrent job

status_keys = {
    'state': lambda t, s: str(s.state),
    'seeds': lambda t, s: [s.num_seeds, s.list_seeds],
    'peers': lambda t, s: [s.num_peers, s.list_peers],
    'copies': 'distributed_copies',
    'seed_rank': 'seed_rank',
    'seed_ratio': lambda t, s: (float(s.all_time_upload)/s.total_wanted) if s.total_wanted else 0.0,

    'num_uploads': 'num_uploads',
    'num_connections': 'num_connections',
    'upload_rate': lambda t, s: t.package.working and s.upload_payload_rate or 0,
    'all_time_upload': 'all_time_upload',
    'last_seen_complete': 'last_seen_complete',
    'total_upload': 'total_upload',
    'total_payload_upload': 'total_payload_upload',
    'total_failed_bytes': 'total_failed_bytes',
    'list_seeds': 'list_seeds',
    'list_peers': 'list_peers'
}


# our torrent package subclass

class TorrentPackage(object):
    pass

for key in status_keys.keys():
    key = 'payload_{}'.format(key)
    col = Column('api')
    setattr(TorrentPackage, key, col)
    col.init_table_class(TorrentPackage, key)


# the torrent handler

class TorrentJob(Torrent):
    def __init__(self, package):
        Torrent.__init__(self, options=dict(save_path=package.get_download_path(), auto_managed=False))

        self.package = package

        try:
            with open(os.path.join(settings.torrent_dir, package.id+'.torrent'), 'rb') as f:
                data = f.read()
        except BaseException as e:
            for file in package.files:
                try:
                    file.fatal('error loading torrent: {}'.format(e))
                except gevent.GreenletExit:
                    pass
            return

        self.state = None
        self.finish_lock = Semaphore()
        self.last_resume_data_save = 0
        
        self.file_objects = dict()
        for f in package.files:
            f._state_download_incomplete = True
            f.progress_initialized = False
            self.file_objects[int(f.split_url.fragment)] = f

        # inject new torrent columns
        if not isinstance(package, TorrentPackage):
            self.package.__class__ = type('TorrentPakage', (core.Package, TorrentPackage), {})
            for key in status_keys.keys():
                key = 'payload_{}'.format(key)
                TorrentPackage.__dict__[key].init_table_instance(self.package)

        self.register_alert('torrent_paused_alert', self.on_torrent_paused_alert)
        self.register_alert('torrent_resumed_alert', self.on_torrent_resumed_alert)

        self._init(data, None)

        torrents[self.id] = self

        if package.state == 'download':
            rename = list()
            for f in self.files:
                try:
                    file = self.file_objects[f['index']]
                except KeyError:
                    continue

                if file.state == 'download':
                    path = file.get_download_file()[len(file.get_download_path())+1:]
                    if path != f['path']:
                        rename.append((f['index'], path))
            if rename:
                self.rename_files(rename)

        self.update_file_priorities()
        self.save_resume_data()
        
        with transaction:
            self.update_status(self.status)
        
        self.update_engine_state()

        on_package_positions_updated()

    # torrent start/stop management

    def update_file_priorities(self):
        enabled = False
        for f in self.files:
            try:
                file = self.file_objects[f['index']]
                if file.enabled and file.state == 'download':
                    priority = 1
                    enabled = True
                else:
                    priority = 0
            except KeyError:
                priority = 0
            finally:
                self.handle.file_priority(f['index'], priority)
        if not enabled:
            self.update_engine_state()

    def update_engine_state(self):
        if config.state == 'started' and self.package.enabled:
            self.resume_automanaged()
            self.on_state_changed()
        else:
            self.pause_automanaged()

    """def update_storage(self):
        save_path = self.info['save_path']
        current_path = self.package.get_download_path()
        if current_path != save_path:
            self.package.log.info('moving to new storage place {}'.format(current_path))
            self.move_storage(current_path)"""

    # package status update

    def on_torrent_paused_alert(self, alert):
        if self.state in ('check', 'download'):
            with transaction:
                for file in self.file_objects.values():
                    if file.working:
                        file.greenlet.kill()

    def on_torrent_resumed_alert(self, alert):
        self.on_state_changed()

    def on_state_changed(self):
        print "!"*100, 'CURRENT_STATE', self.state
        if self.state in ('check', 'download'):
            with transaction:
                for file in self.file_objects.values():
                    if file.enabled and file.state == 'download':
                        if not file.working:
                            file.log.info('file started')
                            file.greenlet = FakeGreenlet(file)
                    elif file.working:
                        file.log.info('file killed {} {} {}'.format(file.enabled, file.state, file.working))
                        file.greenlet.kill()
        #elif self.state in ('seed', 'finish'):
        elif self.state in ('seed',):
            self.update_file_progress()

    def update_status(self, status):
        with transaction:
            for key, value in status_keys.iteritems():
                if callable(value):
                    result = value(self, status)
                else:
                    result = getattr(status, value)
                setattr(self.package, 'payload_{}'.format(key), result)

            if status.need_save_resume and self.last_resume_data_save + 30 < time.time():
                self.save_resume_data(async=True)

            state = str(status.state)
            if self.state != state:
                if 'check' in state or 'metadata' in state or 'allocating' in state:
                    state = 'check'
                elif 'download' in state:
                    state = 'download'
                elif 'seed' in state:
                    state = 'seed'
                #elif 'finish' in state:
                #    state = 'finish'
                else:
                    log.critical('unknown torrent state: {}'.format(state))

                if self.state != state:
                    self.state = state
                    self.on_state_changed()
                    #if status.need_save_resume and self.state in ('download', 'seed', 'finish'):
                    #    self.save_resume_data()

            if state in ('check', 'download'):
                self.update_file_progress()

    def update_file_progress(self):
        #print "update file progress"
        n = 0
        with transaction:
            file_progress = self.file_progress
            if file_progress is None:
                return

            for i in xrange(len(file_progress)):
                try:
                    file = self.file_objects[i]
                except KeyError:
                    continue

                if not file.enabled:
                    continue
                if not file._state_download_incomplete:
                    continue
                if file.state != 'download':
                    continue

                progress = file_progress[i]*file.size
                n += 1

                if self.state == 'check' or not file.progress_initialized or file.progress is None or file._max_progress is None:
                    file.init_progress(file.get_any_size() or 0) # TODO: any size should never be none here
                    file.set_progress(progress)
                    file.progress_initialized = True
                elif file.working:
                    if file.progress != progress:
                        size = progress - file.progress
                        file.register_speed(size > 0 and size or 0)
                        file._last_speed = True
                        file.set_progress(progress)
                    elif file._last_speed:
                        file.register_speed(0)
                        file.set_column_dirty('speed')
                        file._last_speed = file._speed.get_bytes() > 0 and True or False

                #if file.progress == file.get_any_size() and self.state in ('download', 'seed', 'finish'):
                if file.progress == file.get_any_size() and self.state in ('download', 'seed'):
                    file.log.info("file set to complete: {} == {}".format(file.progress, file.get_any_size()))
                    file._state_download_incomplete = False
                    if file.greenlet:
                        file.greenlet.kill()
                    n -= 1
            
        if n == 0:
            #if self.state not in ('seed', 'finish'):
            if self.state not in ('seed',):
                self.state = 'finish'
            gevent.spawn(self.finish)

        #print "update file progress done"

    def finish(self):
        with self.finish_lock:
            if self.package.state != 'download':
                return

            # check if download is complete
            #if any(f._state_download_incomplete or f.working for f in self.package.files if f.enabled and f.state == 'download'):
            if self.package.working or any(f._state_download_incomplete for f in self.package.files):
                print "!"*100, 'finish called but rule not matching'
                print "!"*100, 'finish called but rule not matching state', [g.state for g in self.package.files]
                print "!"*100, 'finish called but rule not matching enabled', [g.enabled for g in self.package.files]
                print "!"*100, 'finish called but rule not matching incomplete', [g._state_download_incomplete for g in self.package.files]
                print "!"*100, 'finish called but rule not matching working', [g.working for g in self.package.files]
                return

            complete_path = self.package.get_complete_path()
            complete_path_len = len(complete_path)

            rename = list()
            for f in self.files:
                try:
                    file = self.file_objects[f['index']]
                except KeyError:
                    continue

                path = file.get_complete_file()[complete_path_len+1:]
                if path != f['path']:
                    rename.append((f['index'], path))
            if rename:
                self.rename_files(rename)

            if self.package.get_download_path() != complete_path:
                print "!"*100, 'MOVE STORAGE'
                self.move_storage(complete_path)

            print "!"*100, 'foo'
            self.pause_automanaged()
            self.save_resume_data()

            print "!"*100, 'bar'
            for file in self.package.files:
                if not file.enabled:
                    continue
                file.log.info('download complete')
                with transaction:
                    file.state = 'download_complete'
                event.fire('file:download_complete', file)

            self.package.log.info('download complete')
            with transaction:
                self.package.state = 'download_complete'
            event.fire('package:download_complete', self.package)

    def save_resume_data(self, async=False):
        Torrent.save_resume_data(self, async=async)
        self.last_resume_data_save = time.time()

    def remove(self):
        try:
            del torrents[self.id]
        except KeyError:
            pass
        Torrent.remove(self)
        # remove torrent columns
        if isinstance(self.package, TorrentPackage):
            self.package.__class__ = type('TorrentPakage', (core.Package,), {})


def init():
    pass

def terminate():
    g = Group()
    for torrent in torrents.values():
        g.spawn(torrent.save_resume_data)
    try:
        g.join(timeout=5)
    except:
        pass
    for torrent in torrents.values():
        torrent.remove()


########### add function. very important, also for plugins

def add_torrent(t, file=None):
    """t = Torrent object instance or string with torrent data"""
    if isinstance(t, str):
        try:
            t = Torrent(data=t, options=dict(save_path=core.config.download_dir, auto_managed=False))
        except RuntimeError:
            if file is not None:
                file.fatal('torrent already exists')
            else:
                log.warning('torrent already exists')
                raise gevent.GreenletExit()

    try:
        name = t.name
        for package in core.packages():
            if package.system == 'torrent' and package.name == name:
                return

        links = list()
        #rename = list()
        for i, f in enumerate(t.files):
            fname = convert_torrent_name(name, f['path'])
            #if fname != f['path']:
            #    rename.append((i, fname))
            link = {}
            link['url'] = 'torrent:///?{}#{}'.format(urllib.quote(t.id), i)
            link['name'] = fname
            link['size'] = f['size']
            link['state'] = 'collect'
            links.append(link)

        #if rename:
        #    t.rename_files(rename)

        t.save_torrent()

        ids = core.add_links(links, package_name=name, package_id=t.id, system='torrent')
        for file in core.files():
            if file.id in ids:
                event.fire('file:checked', file)
    finally:
        t.remove()


# interface

@interface.register
class Interface(interface.Interface):
    name = 'torrent'
    
    def start():
        config.state = 'started'
        
    def pause():
        config.state = 'paused'

    def stop():
        config.state = 'stopped'

    def force_reannounce(**filter):
        filter['system'] = 'torrent'
        for p in core.packages():
            if p.match_filter(**filter):
                id = p.files[0].split_url.query_string
                torrents[id].force_reannounce()

    def get_peers(id=None):
        return torrents[id].peers

    def get_pieces(id=None):
        return torrents[id].pieces
