#!/usr/bin/env python
# encoding: utf-8
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
import sys
import gevent
import itertools
import traceback

from gevent.lock import RLock
from gevent.event import AsyncResult

from ... import core, event, settings, interface, logger, fileplugin
from ...scheme import transaction
from ...config import globalconfig
from ...contrib import rarfile

name = 'rarextract'
priority = 100

log = logger.get("fileplugins.rarextract")
lock = RLock()

config = globalconfig.new("rarextract")
if sys.platform == "win32":
    config.default("rartool", os.path.join(settings.bin_dir, "unrar.exe"), unicode)
elif sys.platform == "darwin":
    config.default("rartool", os.path.join(settings.bin_dir, "unrar_macos"), unicode)
else:
    config.default("rartool", "unrar", unicode)

rarfile.NEED_COMMENTS = 1
rarfile.UNICODE_COMMENTS = 1
rarfile.USE_DATETIME = 1
rarfile.PATH_SEP = '/'
rarfile.UNRAR_TOOL = config["rartool"]

@config.register("rartool")
def changed(value):
    rarfile.UNRAR_TOOL = value

extractors = dict()
blacklist = set()

def match(path, file):
    if not isinstance(file, core.File):
        return False
    if not core.config.autoextract:
        return False
    if path.basename in blacklist:
        return False
    if not rarfile.is_rarfile(path):
        return False
    if "rarextract" in file.completed_plugins:
        return False
    if check_file(path) is None:
        return None
    return True

class StreamingExtract(object):
    def __init__(self, id, hddsem, threadpool):
        self.id = id
        self.hddsem = hddsem
        self.threadpool = threadpool

        self.password = None

        self.killed = False
        self.parts = dict()
        self.first = None
        self.current = None
        self.next = None
        self.next_part_event = AsyncResult()
        self.rar = None
        extractors[id] = self
        
    def feed_part(self, path, file):
        path.finished = AsyncResult()
        self.parts[path.path] = path, file
        log.debug('fed new part {}: {}'.format(path, path))

        if file.state != 'rarextract':
            with transaction:
                file.state = 'rarextract'

        if self.first is None:
            self.current = path, file
            self.run(path, file)
        else:
            if path.path == self.next:
                self.next_part_event.set(path)
            path.finished.get()

    def run(self, path, file):
        try:
            self.first = self.current
            with transaction:
                file.greenlet = gevent.getcurrent()
                file.on_greenlet_started()
            try:
                result = self.bruteforce(path, file)
            except rarfile.NeedFirstVolume:
                self.next = os.path.join(path.dir, "{}.part{}.rar".format(path.basename, "1".zfill(len(path.part))))
                self.find_next()
                if core.config.delete_extracted_archives:
                    return False
                return
            
            if result and result is not True:
                raise result

            if self.password:
                rarpw = "-p"+self.password
            else:
                rarpw = "-p-"

            cmd = [rarfile.UNRAR_TOOL, "x", "-y", rarpw, "-idq", "-vp", path, file.get_extract_path() + os.sep]
            file.log.info("starting extraction of {} with params {}".format(path[1:], cmd))
            self.rar = rarfile.custom_popen(cmd)

            self.wait_data()
            if not path.finished.ready():
                path.finished.set()
            if core.config.delete_extracted_archives:
                return False
        except BaseException as e:
            traceback.print_exc()
            self.kill(e)
            raise
        
    def bruteforce(self, path, file):
        try:
            rar = rarfile.RarFile(path, ignore_next_part_missing=True)
        except rarfile.NeedFirstVolume:
            raise
        if not rar.needs_password():
            self.password = None
            return
        if rar.needs_password() and rar.infolist():
            # unencrypted headers. use file password or ask user.
            pw = None
            if len(file.package.extract_passwords) == 1:
                pw = file.package.extract_passwords[0]
            if not pw:
                for pw in file.solve_password(message="Rarfile {} password cannot be cracked. Enter correct password: #".format(path.name), retries=1):
                    break
                else:
                    return self.kill('extract password not entered')
            self.password = pw
            return
        passwords = []
        for i in itertools.chain(file.package.extract_passwords, core.config.bruteforce_passwords):
            if not i in passwords:
                passwords.append(i)
        print "testing", passwords
        if not self.threadpool.apply(bruteforce, (rar, passwords, self.hddsem, file.log)):
            # ask user for password
            for pw in file.solve_password(message="Enter the extract password for file: {} #".format(path.name), retries=5):
                if self.threadpool.apply(bruteforce, (rar, [pw], self.hddsem, file.log)):
                    break
            else:
                return self.kill('extract password not entered')

        self.password = rar._password
        if self.password and self.password not in core.config.bruteforce_passwords:
            with transaction:
                core.config.bruteforce_passwords.append(self.password)

    def wait_data(self):
        bytes = ''
        while True:
            data = self.rar.stdout.read(1)
            if not data:
                break

            bytes += data
            for i in bytes.splitlines():
                if i:
                    result = self.new_data(i)
                    if result is True:
                        bytes = ''
                    if result and result is not True:
                        raise result
        self.close()

    def finish_file(self, path, file):
        if file is not None:
            with core.transaction:
                #if not 'rarextract' in file.completed_plugins:
                #    file.completed_plugins.append('rarextract')
                #file.greenlet = None
                #file.on_greenlet_finish()
                #file.on_greenlet_stopped()
                file.state = 'rarextract_complete'
                file.init_progress(1)
                file.set_progress(1)
                #file.stop()
        #path.finished.set()
        event.fire('rarextract:part_complete', path, file)
    
    def new_data(self, data):
        """called when new data or new line
        """
        #print "got new data from unrar:", data
        if "packed data CRC failed in volume" in data:
            return self.kill('checksum error in rar archive')
            
        if data.startswith("CRC failed in the encrypted file"): # corrupt file or download not complete
            return self.kill('checksum error in rar archive. wrong password?')

        m = re.search(r"Insert disk with (.*?) \[C\]ontinue\, \[Q\]uit", data)
        if not m:
            return

        if self.current is not None:
            self.finish_file(*self.current)

        self.next = m.group(1)
        print "setting self.next", self.next
        return self.find_next()
        
    def find_next(self):
        print "finding next", self.next
        next = self.next
        if next not in self.parts:
            # check if file is in core.files()
            found = False
            name = os.path.basename(next)
            for f in core.files():
                if f.name == name and f.get_complete_file() == next:
                    found = True
                    if not f.working and 'download' in f.completed_plugins:
                        current = fileplugin.FilePath(next), f
                        current[0].finished = AsyncResult()
                        self.parts[next] = current
                        log.debug('got next part from idle {}: {}'.format(next, self.current[0]))
                        break
            if not found:
                # file is not in system, check if it exists on hdd
                if os.path.exists(next):
                    current = fileplugin.FilePath(next), self.first[1]
                    current[0].finished = AsyncResult()
                    self.parts[next] = current
                    log.debug('got next part from hdd {}: {}'.format(next, self.current[0]))
                else:
                    # part does not exists. fail this extract
                    return self.kill('missing part {}'.format(next))

            if next not in self.parts:
                log.debug('waiting for part {}'.format(next))
                event.fire('rarextract:waiting_for_part', next)
                
                while next not in self.parts:
                    self.next_part_event.get()
                    self.next_part_event = AsyncResult()

                log.debug('got next part from wait {}: {}'.format(next, self.current[0]))

        self.current = self.parts[next]
        return self.go_on()
                
    def go_on(self):
        if self.rar is None:
            return self.run(*self.current)
        if not os.path.exists(self.next):
            return
        self.rar.stdin.write("C\n")
        self.rar.stdin.flush()

        if self.current[1] is not None:
            with core.transaction:
                self.current[1].greenlet = gevent.getcurrent()
                self.current[1].greenlet.link(self.current[0].finished)
                self.current[1].on_greenlet_started()
            self.current[1].log.info("extract go on: {}".format(self.current[1].name))
        return True
        
    def kill(self, exc=""):
        #blacklist.add(self.first[0].basename) # no autoextract for failed archives
        print "killing rarextract", self.first[0].basename
        if isinstance(exc, basestring):
            exc = ValueError(exc)

        self.current = None
        self.killed = True

        if self.rar is not None:
            self.rar.terminate()
            self.rar = None

        try:
            del extractors[self.id]
        except KeyError:
            pass
        
        self.next_part_event.set_exception(exc)
        for path, file in self.parts.values():
            if not path.finished.ready():
                path.finished.set_exception(exc)

        with transaction:
            for path, file in self.parts.values():
                if file is not None:
                    file.stop()
                    if file.state == 'rarextract_complete':
                        file.state = 'rarextract'
                        file.enabled = False
                    print "!"*100, 'FUCK YOU'
                    if 'rarextract' in file.completed_plugins:
                        file.completed_plugins.remove('rarextract')

        self.first[1].fatal('rarextract: {}'.format(exc))

        return exc

    def close(self):
        """called when process is closed"""
        try:
            del extractors[self.id]
        except KeyError:
            pass
        
        if not self.killed:
            if self.current is not None:
                self.finish_file(*self.current)

            if core.config.delete_extracted_archives:
                with transaction:
                    for path, file in self.parts.values():
                        if file:
                            file.delete_local_files()
                            file.fatal('extracted and deleted', type='info', abort_greenlet=False)
                        else:
                            os.remove(path)
            else:
                for path, file in self.parts.values():
                    if file:
                        file.log.info('extract complete')

def check_file(path):
    with lock:
        id = os.path.join(path.dir, path.basename)
        if id in extractors:
            return id
        try:
            rarfile.RarFile(path.path)
        except rarfile.NeedFirstVolume:
            return id
        except IOError:
            # missing multipart archive error?!
            pass
        return id

def process(path, file, hddsem, threadpool):
    print "process rar file", path
    with lock:
        id = os.path.join(path.dir, path.basename) #check_file(path)
        if id not in extractors:
            print "Creating StreamingExtract from", path
            extractors[id] = StreamingExtract(id, hddsem, threadpool)
    extractors[id].feed_part(path, file)

def bruteforce(rar, pwlist, hddsem, log): # runs in threadpool
    if not rar.needs_password():
        return True
    
    had_infolist = bool(rar.infolist())
    for pw in pwlist:
        try:
            rar.setpassword(pw)
        except rarfile.BadRarFile:
            if not rar.infolist():
                reinit(rar)
                continue
            else:
                # bug? seems to be successfully checked. maybe crc error, try extracting anyway
                pass
        if had_infolist and rar.infolist() and test_on_smallest(rar, hddsem, log): # headers not encrypted, test password
            raise NotImplementedError("Cannot bruteforce files with unencrypted headers")
        if not had_infolist and rar.infolist(): # password set successfull
            return True
        reinit(rar)
    print "not found"
    return False

def reinit(rar):
    rar._last_aes_key = (None, None, None)
    rarfile.RarFile.__init__(rar, rar.rarfile, ignore_next_part_missing=True)

def test_on_smallest(rar, hddsem, log):
    return True # this is broken... test later
    smallest = 0
    #smallest_fname = ""
    for i in rar.infolist():
        fname = i.filename
        fsize = i.file_size
        if not smallest or (i.file_size < smallest and i.file_size > 0):
            smallest = fsize
            #smallest_fname = fname
    if smallest > 10*1024**2:
        hddsem.acquire()
        _acquired = True
    else:
        _acquired = False
    try:
        cmd = [rarfile.UNRAR_TOOL] + list(rarfile.TEST_ARGS)
        cmd += ["-y", "-p" + rar._password, rar.rarfile, fname]
        p = rarfile.custom_popen(cmd)
        output = p.communicate()[0]
        try:
            rarfile.check_returncode(p, output)
        except rarfile.RarCRCError:
            return False
        except (KeyboardInterrupt, SystemExit):
            raise
        except: # XXX other errors testing?
            log.exception("test_on_smallest")
            return False
        else:
            return True
    finally:
        if _acquired:
            hddsem.release()

@interface.register
class RarextractInterface(interface.Interface):
    name = "file.rarextract"

    def extract(file=None, password=None):
        pass
