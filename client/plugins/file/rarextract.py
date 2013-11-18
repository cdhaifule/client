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

from ... import core, event, settings, logger, fileplugin
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


@config.register("rartool")
def changed_rartool(value):
    if isinstance(value, unicode):
        value = value.encode(sys.getfilesystemencoding())
    rarfile.UNRAR_TOOL = value

changed_rartool(config["rartool"])

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
        self.library = None
        self._library_added = set()
        self._deleted_library = None
        extractors[id] = self

    def feed_part(self, path, file):
        path.finished = AsyncResult()
        self.parts[path.path] = path, file
        log.debug('fed new part {}: {}'.format(path, path))

        if file.state != 'rarextract':
            with transaction:
                file.state = 'rarextract'

        if self.first is None:
            self.first = self.current = path, file
            self.add_library_files()
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
        rar = rarfile.RarFile(path, ignore_next_part_missing=True)
        if rar.not_first_volume:
            raise rarfile.NeedFirstVolume("First Volume for extraction")

        if not rar.needs_password():
            self.password = None
            return
        passwords = []
        for i in itertools.chain(file.package.extract_passwords, core.config.bruteforce_passwords):
            if not i in passwords:
                passwords.append(i)
        if rar.needs_password() and rar.infolist():
            pw = bruteforce_by_content(rar, passwords)
            if not pw:
                print "could not find password, asking user"
                for pw in file.solve_password(
                        message="Rarfile {} password cannot be cracked. Enter correct password: #".format(path.name),
                        retries=5):
                    pw = bruteforce_by_content(rar, [pw])
                    if pw:
                        break
                else:
                    return self.kill('extract password not entered')
            else:
                print "Found password by content:", pw
            self.password = pw
            return
        print "testing", passwords
        if not self.threadpool.apply(bruteforce, (rar, passwords, self.hddsem, file.log)):
            # ask user for password
            for pw in file.solve_password(
                    message="Enter the extract password for file: {} #".format(path.name),
                    retries=5):
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
        """called when new data or new line"""
        if "packed data CRC failed in volume" in data:
            return self.kill('checksum error in rar archive')

        if data.startswith("CRC failed in the encrypted file"):  # corrupt file or download not complete
            return self.kill('checksum error in rar archive. wrong password?')

        if "bad archive" in data.lower():
            return self.kill('Bad archive')

        m = re.search(r"Insert disk with (.*?((\.part\d+)?\.r..)) \[C\]ontinue\, \[Q\]uit", data)
        if not m:
            return

        if self.current is not None:
            self.finish_file(*self.current)

        self.next = self.first[0].basename + m.group(2)
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
                    if not f.working and 'download' in f.completed_plugins:
                        found = True
                        current = fileplugin.FilePath(next), f
                        current[0].finished = AsyncResult()
                        self.parts[next] = current
                        print('got next part from idle {}: {}'.format(next, self.current[0]))
                        break
                    if f.state == "download":
                        found = True
                        break
                    print "found path but not valid", f.state, f.working

            if not found:
                # file is not in system, check if it exists on hdd
                if os.path.exists(next):
                    current = fileplugin.FilePath(next), self.first[1]
                    current[0].finished = AsyncResult()
                    self.parts[next] = current
                    print('got next part from hdd {}: {}'.format(next, self.current[0]))
                else:
                    # part does not exists. fail this extract
                    return self.kill('missing part {}'.format(next))

            if next not in self.parts:
                print('waiting for part {}'.format(next))
                event.fire('rarextract:waiting_for_part', next)

                @event.register("file:last_error")
                def killit(e, f):
                    if not f.name == name and f.get_complete_file() == next:
                        return
                    if all(f.last_error for f in core.files() if f.name == name and f.get_complete_file() == next):
                        event.remove("file:last_error", killit)
                        self.kill('all of the next parts are broken.')
                
                while next not in self.parts:
                    self.next_part_event.get()
                    self.next_part_event = AsyncResult()

                log.debug('got next part from wait {}: {}'.format(next, self.current[0]))

        self.current = self.parts[next]
        self.add_library_files()
        return self.go_on()

    def add_library_files(self):
        """Add extracted files into the library"""
        path = fileplugin.FilePath(self.current[0])
        f = self.first[1]

        with transaction:
            if not self.library:
                print "Creating package for", path.basename
                name = "{} {}".format("Extracted files from", os.path.basename(path.basename))
                for p in core.packages():
                    if p.name == name:
                        self.library = p
                        self._library_added = set(f.name for f in p.files)
                        print "\treused package", p.id
                        print "package", p.id, p.tab
                if not self.library:
                    self.library = f.package.clone_empty(
                        name=name,
                        tab="complete",
                        state="download_complete",
                    )

                @event.register("package:deleted")
                @event.register("file:deleted")
                def _deleted_library(e, package):
                    import traceback
                    print traceback.print_stack()
                    print "---------", e
                    if e.startswith("file:"):
                        package = package.package

                    if package.id == self.library.id:
                        event.remove("package:deleted", _deleted_library)
                        event.remove("file:deleted", _deleted_library)
                        for f in self.library.files:
                            f.delete_local_files()
                        self.kill("Extracted files have been deleted.", False)

                self._deleted_library = _deleted_library

            rar = rarfile.RarFile(path, ignore_next_part_missing=True)
            print "password is", self.password
            try:
                if not rar.infolist():
                    rar.setpassword(self.password)
            except rarfile.BadRarFile:
                if not rar.infolist():
                    self.library.delete()
                    return
            links = []
            for item in rar.infolist():
                name = item.filename
                print "From new infolist:", name
                if name in self._library_added:
                    print "\t already added"
                    continue
                elif item.isdir():
                    print "\t is dir"
                    continue
                else:
                    self._library_added.add(name)
                print "creating file for", repr(name), self.library
                
                links.append(dict(
                    name=name,
                    size=item.file_size,
                    url=u'file://' + os.path.join(
                        f.get_extract_path().decode(sys.getfilesystemencoding()),
                        name),
                ))
        if links:
            core.add_links(links, package_id=self.library.id)

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
        
    def kill(self, exc="", _del_lib=True):
        if self.killed:
            return exc
        self.killed = True

        blacklist.add(self.first[0].basename)  # no autoextract for failed archives
        if _del_lib:
            self.library.delete()
        print "killing rarextract", self.first[0].basename, exc
        if isinstance(exc, basestring):
            exc = ValueError(exc)

        self.current = None

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
                    if 'rarextract' in file.completed_plugins:
                        file.completed_plugins.remove('rarextract')

        self.first[1].fatal('rarextract: {}'.format(exc))

        return exc

    def close(self):
        """called when process is closed"""
        if not self.library:
            self.add_library_files()
        try:
            del extractors[self.id]
        except KeyError:
            pass

        if self._deleted_library:
            event.remove("package:deleted", self._deleted_library)

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
        id = os.path.join(path.dir, path.basename)  # check_file(path)
        if id not in extractors:
            print "Creating StreamingExtract from", path
            try:
                blacklist.remove(path.basename)
            except KeyError:
                pass
            extractors[id] = StreamingExtract(id, hddsem, threadpool)
    print "FEED!", path
    extractors[id].feed_part(path, file)


def bruteforce(rar, pwlist, hddsem, log):  # runs in threadpool
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
        if had_infolist and rar.infolist():  # headers not encrypted, test password
            raise NotImplementedError("Cannot bruteforce files with unencrypted headers")
        if not had_infolist and rar.infolist():  # password set successfull
            return True
        reinit(rar)
    print "not found"
    return False


def test_m2ts(content):
    return all(content.find("\x47", i, 2000) == i for i in (4, 196, 388, 580))


def _test_passwords(rar, fname, passwords):
    try:
        from libmagic import from_buffer
    except ImportError:
        return False
    ext = fname.rsplit(".", 1)[-1]
    for pw in passwords:
        cmd = [rarfile.UNRAR_TOOL, "-ierr", "-p"+pw, "-y", "p", rar.rarfile, fname]
        
        try:
            p = None
            with gevent.Timeout(10):
                p = rarfile.custom_popen(cmd, -1)
                data = p.stdout.read(1024*1024)
        except (IOError, OSError, gevent.Timeout) as e:
            print "popen failed for", cmd, e
            #traceback.print_exc()
            if p:
                try:
                    p.kill()
                except:
                    pass
            continue

        if not data.strip():
            print "killing process"
            try:
                p.kill()
            except:
                pass
            continue
        if ext == "m2ts":
            if test_m2ts(data):
                return pw
            else:
                continue
        result = from_buffer(data)
        p.kill()
        desc = result.description
        print "magic desc is", desc
        print "ext is", ext
        print "mime is:", result.mimetype
        if desc == "data":
            continue
        elif ext in extensions:  # enforce mime type for extensions
            print "ext is", ext
            if result.mimetype != extensions[ext]:
                continue
            else:
                print "found password", pw
                return pw
        else:
            # may get false positives
            print "found password", pw
            return pw  # return pw for everything besides random application data
    return False


def bruteforce_by_content(rar, passwords):
    def _sort(k):
        ext = k.filename.rsplit(".", 1)[-1]
        k1 = ext not in extensions
        k2 = k.file_size
        k3 = k.filename
        return (k1, k2, k3)

    for i in sorted(rar.infolist(), key=_sort):
        pw = _test_passwords(rar, i.filename, passwords)
        if pw:
            return pw
    return False


def reinit(rar):
    rar._last_aes_key = (None, None, None)
    rarfile.RarFile.__init__(rar, rar.rarfile, ignore_next_part_missing=True)

extensions = {
    '3gp': 'video/3gpp',
    'ai': 'application/postscript',
    'aif': 'audio/x-aiff',
    'aifc': 'audio/x-aiff',
    'aiff': 'audio/x-aiff',
    'asc': 'application/pgp-signature',
    'asf': 'video/x-ms-asf',
    'asx': 'video/x-ms-asf',
    'au': 'audio/basic',
    'avi': 'video/x-msvideo',
    'boz': 'application/x-bzip2',
    'bz2': 'application/x-bzip2',
    'cab': 'application/vnd.ms-cab-compressed',
    'cpio': 'application/x-cpio',
    'deb': 'application/x-debian-package',
    'djv': 'image/vnd.djvu',
    'djvu': 'image/vnd.djvu',
    'doc': 'application/msword',
    'dot': 'application/msword',
    'dvi': 'application/x-dvi',
    'eml': 'message/rfc822',
    'eps': 'application/postscript',
    'f': 'text/x-fortran',
    'f77': 'text/x-fortran',
    'f90': 'text/x-fortran',
    'flac': 'audio/x-flac',
    'fli': 'video/x-fli',
    'flv': 'video/x-flv',
    'for': 'text/x-fortran',
    'gif': 'image/gif',
    'gnumeric': 'application/x-gnumeric',
    'gv': 'text/vnd.graphviz',
    'h264': 'video/h264',
    'hdf': 'application/x-hdf',
    'hqx': 'application/mac-binhex40',
    'htm': 'text/html',
    'html': 'text/html',
    'iso': 'application/x-iso9660-image',
    'jpe': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'jpg': 'image/jpeg',
    'kar': 'audio/midi',
    'kml': 'application/vnd.google-earth.kml+xml',
    'kmz': 'application/vnd.google-earth.kmz',
    'lwp': 'application/vnd.lotus-wordpro',
    'm1v': 'video/mpeg',
    'm2a': 'audio/mpeg',
    'm2v': 'video/mpeg',
    'm2ts': 'video/MP2T',
    'm3a': 'audio/mpeg',
    'man': 'text/troff',
    'mdb': 'application/x-msaccess',
    'me': 'text/troff',
    'mid': 'audio/midi',
    'midi': 'audio/midi',
    'mime': 'message/rfc822',
    'mk3d': 'video/x-matroska',
    'mks': 'video/x-matroska',
    'mkv': 'video/x-matroska',
    'mng': 'video/x-mng',
    'mov': 'video/quicktime',
    'movie': 'video/x-sgi-movie',
    'mp2': 'audio/mpeg',
    'mp2a': 'audio/mpeg',
    'mp3': 'audio/mpeg',
    'mp4': 'video/mp4',
    'mp4a': 'audio/mp4',
    'mp4v': 'video/mp4',
    'mpe': 'video/mpeg',
    'mpeg': 'video/mpeg',
    'mpg': 'video/mpeg',
    'mpg4': 'video/mp4',
    'mpga': 'audio/mpeg',
    'ms': 'text/troff',
    'nfo': 'text/plain',
    'odb': 'application/vnd.oasis.opendocument.database',
    'odc': 'application/vnd.oasis.opendocument.chart',
    'odf': 'application/vnd.oasis.opendocument.formula',
    'odft': 'application/vnd.oasis.opendocument.formula-template',
    'odg': 'application/vnd.oasis.opendocument.graphics',
    'odi': 'application/vnd.oasis.opendocument.image',
    'odm': 'application/vnd.oasis.opendocument.text-master',
    'odp': 'application/vnd.oasis.opendocument.presentation',
    'ods': 'application/vnd.oasis.opendocument.spreadsheet',
    'odt': 'application/vnd.oasis.opendocument.text',
    'ogx': 'application/ogg',
    'otc': 'application/vnd.oasis.opendocument.chart-template',
    'otg': 'application/vnd.oasis.opendocument.graphics-template',
    'oth': 'application/vnd.oasis.opendocument.text-web',
    'oti': 'application/vnd.oasis.opendocument.image-template',
    'otp': 'application/vnd.oasis.opendocument.presentation-template',
    'ots': 'application/vnd.oasis.opendocument.spreadsheet-template',
    'ott': 'application/vnd.oasis.opendocument.text-template',
    'pbm': 'image/x-portable-bitmap',
    'pdf': 'application/pdf',
    'pgp': 'application/pgp-encrypted',
    'png': 'image/png',
    'ppm': 'image/x-portable-pixmap',
    'ps': 'application/postscript',
    'psd': 'image/vnd.adobe.photoshop',
    'qt': 'video/quicktime',
    'ra': 'audio/x-pn-realaudio',
    'ram': 'audio/x-pn-realaudio',
    'rm': 'application/vnd.rn-realmedia',
    'rmi': 'audio/midi',
    'roff': 'text/troff',
    'sig': 'application/pgp-signature',
    'sis': 'application/vnd.symbian.install',
    'sisx': 'application/vnd.symbian.install',
    'sit': 'application/x-stuffit',
    'snd': 'audio/basic',
    'svg': 'image/svg+xml',
    'svgz': 'image/svg+xml',
    'swf': 'application/x-shockwave-flash',
    't': 'text/troff',
    'tfm': 'application/x-tex-tfm',
    'tif': 'image/tiff',
    'tiff': 'image/tiff',
    'torrent': 'application/x-bittorrent',
    'tr': 'text/troff',
    'ttc': 'application/x-font-ttf',
    'ttf': 'application/x-font-ttf',
    'udeb': 'application/x-debian-package',
    'vcf': 'text/x-vcard',
    'vob': 'video/mpeg',
    'vrml': 'model/vrml',
    'wav': 'audio/x-wav',
    'wrl': 'model/vrml',
    'xla': 'application/vnd.ms-excel',
    'xlc': 'application/vnd.ms-excel',
    'xlm': 'application/vnd.ms-excel',
    'xls': 'application/vnd.ms-excel',
    'xlt': 'application/vnd.ms-excel',
    'xlw': 'application/vnd.ms-excel',
    'xml': 'application/xml',
    'xsl': 'application/xml',
    'xz': 'application/x-xz',
    'zip': 'application/zip'
}
