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
import math
import shutil

from .. import core, event, ratelimit, plugintools, logger
from ..scheme import transaction, intervalled
from ..config import globalconfig
from ..contrib import sizetools
from ..variablesizepool import VariableSizePool

import gevent
from gevent.lock import Semaphore
from gevent.event import Event
from gevent.threadpool import ThreadPool


log = logger.get('download')
pool = VariableSizePool(size=2)
lock = Semaphore()


########################## config

config = globalconfig.new('download')
config.default('state', 'started', str) # started|paused|stopped
config.default('max_simultan_downloads', 2, int)
config.default('max_chunks', 1, int)
config.default('min_chunk_size', sizetools.KB(500), int)
config.default('blocksize', sizetools.KB(8), int)
config.default('overwrite', 'ask', str)  # ask|skip|rename|overwrite
config.default('max_retires', 3, int)
config.default('rate_limit', 0, int)

@config.register('max_simultan_downloads')
def config_max_simultan_downloads(value):
    if value <= 0:
        config.max_simultan_downloads = 0
    if value > 20:
        config.max_simultan_downloads = 20
    pool.set(config.max_simultan_downloads)
    event.fire_once_later(0.5, 'download:spawn_tasks')

@config.register('max_chunks')
def config_max_chunks(value):
    if value <= 0:
        config.max_chunks = 0
    if value > 20:
        config.max_chunks = 20

@config.register('rate_limit')
def config_rate_limit(value):
    if value > 0 and value < config.blocksize:
        config.rate_limit = config.blocksize
    ratelimit.set_rate(config.rate_limit)


########################## spawn strategy

working_downloads = list()

class Strategy(object):
    def __init__(self):
        self.on()

    def on(self):
        self.type = 'on'
        self.callbacks = dict()

    def only_premium(self, id, func, *args, **kwargs):
        if self.type == 'off':
            self.off(func, *args, **kwargs)
        else:
            self.type = 'only_premium'
            self.callbacks[id] = (func, args, kwargs)
            event.fire('download.spawn_strategy:changed')
            self._check_only_premium()

    def off(self, id, func, *args, **kwargs):
        self.type = 'off'
        self.callbacks[id] = (func, args, kwargs)
        event.fire('download.spawn_strategy:changed')
        self._check_off()

    def has(self, id):
        return self.type != 'on' and id in self.callbacks

    def pop(self, id):
        if id not in self.callbacks:
            return
        del self.callbacks[id]
        if not self.callbacks:
            self.on()

    def check(self):
        if self.type == 'only_premium':
            self._check_only_premium()
        elif self.type == 'off':
            self._check_off()

    def _check_only_premium(self):
        free_running = any(True for f in working_downloads if not hasattr(f.account, 'premium') or not f.account.premium or not f.can_resume)
        if not free_running:
            for func, args, kwargs in self.callbacks.values():
                func(*args, **kwargs)
        self.on()

    def _check_off(self):
        for f in working_downloads:
            if f.working:
                return
        for func, args, kwargs in self.callbacks.values():
            func(*args, **kwargs)
        self.on()

strategy = Strategy()

@event.register('file:download_task_done')
def strategy_check(e, file):
    gevent.spawn_later(0.1, strategy.check)


########################## start/pause/stop

def start():
    config.state = 'started'
    strategy.pop('pause')
    event.fire('download:started')
    log.info("started")

def pause():
    config.state = 'paused'
    strategy.off('pause', stop)
    event.fire('download:paused')
    log.info("paused")

def stop():
    config.state = 'stopped'
    strategy.pop('pause')
    for file in core.files():
        file.stop(_stop_fileplugins=False)
    event.fire('download:stopped')
    log.info("stopped")

@config.register('state')
def config_state_changed(value, old):
    if value == old:
        return
    if config.state == 'started':
        start()
    elif config.state == 'paused':
        pause()
    elif config.state == 'stopped':
        stop()
    else:
        raise RuntimeError('invalid state: {}'.format(config.state))


########################## the default stream download function (can be extended)

class DownloadFunction(intervalled.Cache):
    def __init__(self, input):
        self.input = input
        self.output = None
        self.chunk = None
        self.last_read = 0
        self.last_write = 0

    def get_read_size(self):
        if self.chunk.end is None:
            size = config["blocksize"]
        else:
            remaining = self.chunk.end - (self.chunk.pos + self.last_read)
            if remaining == 0:
                for next in self.chunk.file.chunks:
                    if next.begin != self.chunk.end:
                        continue
                    if next.working:
                        break
                    if self.chunk.file.can_resume:
                        progress = next.pos - next.begin
                        if progress > (self.chunk.file.speed/self.chunk.file.chunks_working)*3:
                            break
                    else:
                        progress = 0
                    self.chunk.log.info('merging with next chunk {} (overwriting progress of {} bytes)'.format(next.id, progress))
                    with transaction:
                        self.chunk.end = next.end
                        next.delete()
                    self.reinit_progress()
                    return self.get_read_size()

            size = remaining > config["blocksize"] and config["blocksize"] or remaining
        return size

    def read(self):
        """only register last read size"""
        size = self.get_read_size()
        if size == 0:
            return
        data = self.input.read(size)
        size = len(data)
        if size == 0:
            return
        ratelimit.sleep(size)
        self.last_read += size
        return data

    def write(self, data):
        size = len(data)
        if size:
            self.output.write(data, self.chunk.pos + self.last_write)
            self.last_write += size
        return size

    def reinit_progress(self):
        self.chunk.file.set_progress(sum(chunk.pos - chunk.begin for chunk in self.chunk.file.chunks))

    def process(self):
        for data in iter(self.read, None):
            self.write(data)

    def commit(self):
        """the intervalled commit function"""
        if self.last_read:
            self.chunk.file.add_progress(self.last_read)
            self.chunk.file.register_speed(self.last_read)
            self.chunk.file._last_speed = True
            self.last_read = 0
        elif self.chunk.file._last_speed:
            self.chunk.file.register_speed(0)
            self.chunk.file.set_column_dirty('speed')
            self.chunk.file._last_speed = self.chunk.file._speed.get_bytes() > 0 and True or False

        if self.last_write:
            self.chunk.pos += self.last_write
            if self.chunk.file.size is None and self.chunk.file.approx_size < self.chunk.pos:
                self.chunk.file.approx_size = self.chunk.pos
            self.last_write = 0


########################## close streams

def close_stream(stream):
    if stream:
        if hasattr(stream, 'release_conn') and callable(stream.release_conn): # requests.response.raw object
            stream.release_conn()
        elif hasattr(stream, 'close') and callable(stream.close):
            stream.close()


########################## the main stream download class

class FileDownload(object):
    copypool = ThreadPool(2)
    
    def __init__(self, file):
        self.file = file
        self.account = self.file.account

        file.can_resume = file.can_resume and self.account.can_resume and True or False
        
        if file.max_chunks is None:
            file.max_chunks = self.account.max_chunks
        if file.max_chunks is None or file.max_chunks > config['max_chunks']:
            file.max_chunks = config['max_chunks']
        
        self.pool = VariableSizePool(file.max_chunks)
        self.event = Event()

        self.stream = None
        self.next_data = None

    def init(self):
        with transaction:
            #self.set_context()
            chunk = self.get_first_chunk()

        if chunk is None: # all chunks have state 'complete'
            return

        # initialize the first download
        self.stream, self.next_data = self._error_handler(chunk, self.download, chunk)

        if self.file.size is None:
            self.file.can_resume = False
            self.file.max_chunks = 1
            self.pool.set(1)

        self.file.log.debug('resume possible: {}, size: {}'.format(self.file.can_resume, self.file.size))

        with transaction:
            # create all other chunks
            if self.create_chunks(chunk) == 'retry':
                return 'retry'

            # update file progress
            if not self.file.size is None:
                self.file.init_progress(self.file.size)
            self.file.set_progress(sum([c.pos - c.begin for c in self.file.chunks]))

            # start first (already initialized) download greenlet
            chunk.spawn(self.download_chunk, chunk, stream=self.stream)
            self.pool.add(chunk.greenlet)

        return self.run()

    def spawn_chunk_download(self):
        """if self.file.is_paused():
            return 0"""
        new = 0
        for chunk in self.file.chunks:
            if self.pool.full():
                break
            if chunk.working:
                continue
            if not chunk.next_try is None and chunk.next_try > time.time():
                continue
            if chunk.state != 'download':
                continue
            chunk.next_try = None
            chunk.last_error = None
            chunk.need_reconnect = False
            chunk.spawn(self.download_chunk, chunk)
            self.pool.add(chunk.greenlet)
            new += 1
        return new

    def run(self):
        while True:
            self.pool.wait_available()
            with transaction:
                started = self.spawn_chunk_download()
            if len(self.pool) == 0:
                break
            if started == 0:
                self.event.wait()
                self.event.clear()

        self.pool.join()  # TODO: really necessary?

        self.file.log.debug('all chunk download greenlets are done')

    def finish(self):
        """clean up chunks and set errors/next_try to file"""
        if self.stream:
            close_stream(self.stream)

        if len(self.file.chunks) == 0:
            self.file.retry('download finish got no chunks', 90)

        complete = True
        next_try = None
        need_reconnect = False
        last_error = None

        with transaction:
            for chunk in self.file.chunks:
                if chunk.state != 'complete':
                    complete = False
                    if chunk.next_try and (next_try is None or next_try > chunk.next_try):
                        next_try = chunk.next_try
                        need_reconnect = chunk.need_reconnect
                        last_error = chunk.last_error
                    elif chunk.last_error and not last_error:
                        last_error = chunk.last_error
                chunk.next_try = None
                chunk.need_reconnect = False
                chunk.last_error = None

            if complete:
                self.finalize_complete_download()
                self.file.state = 'download_complete'
                self.file.delete_chunks()
                self.file.fire_after_greenlet('file:download_complete', self.file)

                # check if package is complete
                complete = True
                for f in self.file.package.files:
                    if f.enabled and 'download' not in f.completed_plugins:
                        complete = False
                if complete:
                    self.file.package.state = 'download_complete'
                    self.file.fire_after_greenlet('package:download_complete', self.file.package)

        if not complete:
            if next_try:
                self.file.retry(last_error, next_try - time.time(), need_reconnect)
            elif last_error:
                self.file.fatal(last_error)

    def finalize_complete_download(self):
        if self.file.package.system == 'torrent':
            return
        
        self.file.log.debug('download complete')

        if self.file.filehandle.f is not None:
            self.log.critical('filehandle still open, refcount: {}, handle: {}'.format(self.file.filehandle.refcount, self.file.filehandle.f))
        elif self.file.filehandle.refcount != 0:
            self.log.critical('filehandle still open, refcount: {}, handle: {}'.format(self.file.filehandle.refcount, self.file.filehandle.f))

        download_file = self.file.get_download_file()

        with transaction:
            # disable all other files in group
            hostname = self.file.host.get_hostname(self.file)
            for f in core.files():
                if f != self.file and f.get_download_file() == download_file:
                    f.fatal('downloaded via {}'.format(hostname), abort_greenlet=False)

            # delete file chunks
            with transaction:
                self.file.delete_chunks()

        # remove .dlpart extension from filename and move to completed files directory
        if os.path.exists(download_file):
            complete_file = self.file.get_complete_file()
            if download_file != complete_file:
                try:
                    self.forced_rename()
                except:
                    import traceback
                    traceback.print_exc()
                    raise
            # TODO: delete empty directories

    #########################################################
    
    def forced_rename(self):
        download_file = self.file.get_download_file()
        complete_file = self.file.get_complete_file()
        path = os.path.dirname(complete_file)
        if not os.path.exists(path):
            try:
                os.makedirs(path)
            except (IOError, OSError) as e:
                self.file.fatal("Error creating output directory: {}".format(e))
                return
        try:
            self.copypool.apply_e((IOError, OSError), shutil.move, (download_file, complete_file))
        except (OSError, IOError):
            self.file.log.info("error moving file, try to copy")
            try:
                self.copypool.apply_e((IOError, OSError), shutil.copy, (download_file, complete_file))
            except (IOError, OSError) as e:
                self.file.fatal("Error creating complete file: {}".format(e))
        print 8

    def _error_handler(self, chunk, func, *args, **kwargs):
        try:
            return plugintools.ctx_error_handler(chunk, func, *args, **kwargs)
        except plugintools.RangeNotSatisfiedError:
            if chunk.pos == 0:
                chunk.fatal('range request not satisfied')
            if len(self.file.chunks) == 1:
                with transaction:
                    chunk.pos = 0
                chunk.retry('range request not satisfied', 1)

            for prev in self.file.chunks:
                if prev.end != chunk.begin:
                    continue
                chunk.log.info('range request not satisfied; merging with previous chunk {}'.format(prev.id))
                with transaction:
                    prev.end = chunk.end
                    if prev.state == 'complete':
                        prev.state = 'download'
                self.file.max_chunks -= 1
                chunk.delete_after_greenlet()
                return 'deleted'
            
            chunk.retry('range request not satisfied', 60)

    def get_first_chunk(self):
        # check file on hdd (if exists)
        if len(self.file.chunks) > 0:
            path = self.file.get_download_file()
            if not os.path.exists(path):
                self.file.log.warning('file not found on hdd. resetting chunks')
                self.file.delete_chunks()

        if not self.file.can_resume or len(self.file.chunks) == 0:
            # create a first chunk
            with transaction:
                if self.file.chunks:
                    self.file.log.warning('cannot resume or chunks are zero. resetting chunks')
                self.file.delete_chunks()
                chunk = core.Chunk(file=self.file, end=self.file.size)
        else:
            # get first incomplete chunk
            with transaction:
                self.file.chunks = sorted(self.file.chunks, lambda a, b: a.pos - b.pos)
            all_complete = True
            for chunk in self.file.chunks:
                if chunk.state != 'complete':
                    all_complete = False
                if chunk.state == 'download':
                    break
            else:
                if not all_complete:
                    raise ValueError('not possible exception: found no initial chunk')
                return None # assume all chunks are complete
            chunk.last_error = None
            chunk.need_reconnect = None

        return chunk

    def _create_one_chunk(self):
        if len(self.file.chunks) > 1:
            raise ValueError('create_one_chunk called while chunk count > 1')
        elif len(self.file.chunks) == 1:
            self.file.chunks[0].end = self.file.size
            if not self.file.can_resume and self.file.chunks[0].begin > 0:
                self.file.chunks[0].begin = 0
                return 'retry'

    def create_chunks(self, first_chunk):
        """returns true when first chunk was modified
        """
        num = self.file.max_chunks
        result = None

        if not self.file.can_resume:
            if num > 1:
                self.file.log.debug('cannot resume or filesize is null. using only one chunk')
                num = 1
        elif num > 1:
            block = int(math.ceil(self.file.size/num))
            while num > 1 and block < config['min_chunk_size']:
                num -= 1
                block = int(math.ceil(self.file.size/num))
            self.file.log.debug('using {} chunks with blocksize of {}'.format(num, block))

        self.file.max_chunks = num
        self.pool.set(num)

        if not self.file.can_resume and first_chunk.pos > 0:
            self.file.log.debug('first chunk is at position {} but we cannot resume. resetting chunks'.format(first_chunk.pos))
            self.file.delete_chunks()
            return 'retry'

        self.file.chunks = sorted(self.file.chunks, lambda a, b: a.pos - b.pos)

        if len(self.file.chunks) == num and self.file.chunks[-1].end == self.file.size:
            return

        if num == 1:
            if len(self.file.chunks) == 0:
                self.file.log.debug('created one single brand new chunk')
                core.Chunk(file=self.file, begin=0, end=self.file.size)
            elif self.file.chunks[-1].end != self.file.size:
                self.file.log.debug('chunk(s) are nearly setup correctly. set end from {} to {}'.format(self.file.chunks[-1].end, self.file.size))
                self.file.chunks[-1].end = self.file.size
            else:
                self.file.log.debug('chunk(s) already setup correctly')
            return result

        #if len(self.file.chunks) < num:
        if True:
            begin = 0
            for i in range(num):
                if i == num - 1:
                    end = self.file.size
                else:
                    end = begin + block
                try:
                    chunk = self.file.chunks[i]
                    if chunk.begin > begin or chunk.end < begin:
                        if chunk == first_chunk:
                            result = 'retry'
                        chunk.pos = begin
                    chunk.log.debug('changing begin {}, end {} to begin {}, end {}'.format(chunk.begin, chunk.end, begin, end))
                    if chunk.pos < begin or chunk.pos > end or chunk.begin < begin:
                        chunk.pos = begin
                    elif chunk.pos > chunk.begin:
                        chunk.log.debug('leaving position {} untouched'.format(chunk.pos))
                    chunk.begin = begin
                    chunk.end = end
                except IndexError:
                    self.file.log.debug('creating new chunk begin {}, end {}'.format(begin, end))
                    core.Chunk(file=self.file, begin=begin, end=end)
                begin += block

        return result

    #########################################################

    def download(self, chunk):
        chunk.set_substate('init')

        result = self.file.account.on_download_decorator(self.file.download_func, chunk)
        stream, next_data = self.file.host.handle_download_result(chunk, result)

        return stream, next_data

    def download_next(self, chunk):
        chunk.set_substate('init')
        return self.file.download_next_func(chunk, self.next_data)

    def download_chunk(self, chunk, stream=None):
        try:
            if stream is None:
                chunk.log.info('opening download from {} to {}'.format(chunk.pos, chunk.end))
                stream = self._error_handler(chunk, self.file.account.on_download_next_decorator, self.download_next, chunk)
                if stream == 'deleted':
                    return
            if stream is None:
                chunk.plugin_out_of_date(msg='stream have not to be none')
            chunk.log.info('starting download from {} to {}'.format(chunk.pos, chunk.end))
            chunk.set_substate('running')
            self._error_handler(chunk, self._download_chunk, chunk, stream)
        except plugintools.NoMoreConnectionsError:
            if self.file.max_chunks == 1:
                chunk.retry('no more connections allowed', 90)
            else:
                self.file.max_chunks -= 1
                self.pool.set(self.file.max_chunks)
        finally:
            with transaction:
                close_stream(stream)
                chunk.set_substate()
            gevent.spawn(self.event.set)

    def _download_chunk(self, chunk, input):
        if isinstance(input, DownloadFunction) or hasattr(input, "process"):
            dlfunc = input
        else:
            # use default download function. dlfunc is a stream
            dlfunc = DownloadFunction(input)

        dlfunc.chunk = chunk

        with dlfunc, chunk.file.filehandle as output:
            dlfunc.output = output
            dlfunc.process()

        if not chunk.end is None and chunk.end != chunk.pos:
            chunk.retry('chunk is incomplete (pos {} != end {})'.format(chunk.pos, chunk.end), 60)

        with transaction:
            chunk.state = 'complete'

        event.fire('chunk:download_complete', chunk)


########################## starts a stream download

def download_file(file):
    # find all file mirrors and reset the chunks
    with transaction:
        download_file = file.get_download_file()
        for f in core.files():
            if f != file and f.get_download_file() == download_file:
                f.delete_chunks()

    # start the download process
    dl = FileDownload(file)
    try:
        while dl.init() == 'retry':
            gevent.sleep(0.1)
    except gevent.GreenletExit:
        raise
    except plugintools.NoMoreConnectionsError:
        file.retry('no more connections allowed', 90)
    except BaseException as e:
        try:
            file.plugin_out_of_date(msg='unhandled exception: {}'.format(e), seconds=1800, backend_report=False)
        except gevent.GreenletExit:
            pass
        raise e
    finally:
        if file.account:
            file.account.download_pool._discard(gevent.getcurrent())
            file.account = None
        dl.pool.kill()
        try:
            dl.finish()
        except gevent.GreenletExit:
            pass
        except BaseException as e:
            #file.unhandled_exception('error finishing download: {}'.format(e))
            file.unhandled_exception()
        if file.state == 'download_complete':
            file.reset_progress()
        event.fire('file:download_task_done', file)
