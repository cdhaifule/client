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
import json
import base64
import urlparse
import traceback

from gevent import threadpool, monkey

from .engine import log, DownloadFunction
from .. import event, ratelimit
from ..scheme import transaction

try:
    if "nose" not in sys.argv[0]:
        if sys.platform == "win32":
            from ..settings import app_dir
            import pylibrtmp
            pylibrtmp.tmpdir = os.path.join(app_dir, "lib", "pylibrtmp")
        from pylibrtmp.client import RTMPClient, RTMPError, RTMPResumeError, RTMP_LogSetLevel, RTMP_LOGDEBUG
        RTMP_LogSetLevel(RTMP_LOGDEBUG)
        pylibrtmp = True
    else:
        pylibrtmp = None
except:
    print "Warning: No RTMP Support."
    pylibrtmp = None
    traceback.print_exc()


def rtmplink(url, **options):
    options["_url"] = url
    return "rtmplink://rtmp/" + base64.urlsafe_b64encode(json.dumps(options))

def is_rtmplink(l):
    return l.startswith("rtmplink://")

def load_rtmplink(l):
    assert is_rtmplink(l)
    options = json.loads(base64.urlsafe_b64decode(l[16:]))
    return options.pop("_url"), options
    
def tlog(msg, f=log.debug):
    event.call_from_thread(log.debug, msg)

class RTMPDownload(DownloadFunction):
    def __init__(self, url, **options):
        if pylibrtmp is None:
            raise RuntimeError("No RTMP Download support.")
        
        DownloadFunction.__init__(self, None)
        print "RTMP Download init"
        url = url.encode("utf-8")
        try:
            url, query = url.split("?", 1)
        except (TypeError, ValueError):
            options = dict()
        else:
            options = dict(urlparse.parse_qsl(query))
        if is_rtmplink(url):
            url, options = load_rtmplink(url)
        self.url = url
        self.options = options
        self.rtmp = None
        self.last_index = 0
        self.next_update = 1
        if not "tcurl" in options:
            options["tcurl"] = self.url
        if "swfurl" in options and not "swfvfy" in options:
            options["swfvfy"] = "1"
        self.thread = threadpool.ThreadPool(1)
        self.stopped = False
        
    def process(self):
        if len(self.chunk.file.chunks) > 1:
            raise RuntimeError("Must be only 1 chunk, define max_chunks = 1 in hoster.")
        if not self.chunk.file.name.endswith(".flv"):
            self.chunk.file.set_infos(name=self.chunk.file.name + ".flv")
        print "process thread"
        with transaction:
            self.chunk.end = None
        try:
            return self.thread.spawn(self._process).get()
        finally:
            self.stopped = True
            
    def _process(self):
        """runs in a thread, careful"""
        self.output.close()
        tlog("Thread started, creating RTMPClient")
        self.rtmp = RTMPClient(self.url.encode("utf-8"), **self.options)
        try:
            self.rtmp.connect()
        except RTMPError:
            raise
            event.call_from_thread(self.chunk.file.set_offline, "cannot connect")
            return
        path = self.chunk.file.get_download_file()
        startat = 0
        try:
            if os.path.isfile(path) and self.chunk.pos > 0:
                startat = self.rtmp.resumefrom(path)
        except RTMPResumeError as e:
            tlog("no resume, beginning from start " + e.message)
        try:
            if startat > 0:
                output = open(path, "r+b")
                output.seek(self.rtmp.tell())
                self.last_index = self.rtmp.tell()
            else:
                output = open(path, "wb+")
        except (OSError, IOError) as e:
            tlog("error opening file: " + traceback.format_exc(), log.error)
            event.call_from_thread(self.chunk.file.fatal, e.strerror)
            return
             
        try:
            self.rtmp.connectstream(startat)
            for buf in iter(self.rtmp.read, ""):
                output.write(buf)
                if self.stopped:
                    output.close()
                    break
                ratelimit.sleep(len(buf), sleepfunc=monkey.get_original("time", "sleep"))
            else:
                output.close()
                event.call_from_thread(self.finalize)
        finally:
            self.rtmp.close()
            output.close()
            
        tlog("download rtmp finished")
        return
        
    def reinit_progress(self):
        self.chunk.file.init_progress(self.rtmp.approximate_size, self.rtmp.loaded)
        
    def finalize(self):
        self.chunk.file.set_infos(size=self.rtmp.loaded)
        with transaction:
            self.chunk.pos = self.rtmp.loaded
            self.chunk.end = self.rtmp.loaded
        
    def commit(self):
        if not self.rtmp:
            return
        if not self.rtmp.rtmp:
            self.finalize()
            return
        loaded = self.rtmp.loaded - self.last_index
        self.chunk.file.register_speed(loaded)
        self.last_index = self.rtmp.loaded
        if self.next_update > 1:
            self.chunk.file.set_progress(self.rtmp.loaded)
            #print "set progress?", self.rtmp.loaded
        self.chunk.pos = self.rtmp.loaded
        percent = int(self.rtmp.percent * 100)
        #print percent
        if percent >= self.next_update:
            self.chunk.file.set_infos(approx_size=int(self.rtmp.approximate_size))
            self.reinit_progress()
            self.next_update += 10
