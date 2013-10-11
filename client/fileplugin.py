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

import sys
import os
import bisect
import gevent
import tempfile
import base64
import webbrowser

from collections import defaultdict
from gevent.pool import Group
from gevent.lock import Semaphore
from gevent.threadpool import ThreadPool
from gevent import subprocess

from . import core, variablesizepool, plugintools, interface, event, logger, login
from .scheme import transaction
from .config import globalconfig

log = logger.get('loader')

config = globalconfig.new('file')

class FilePath(str):
    """File's path string with stat infos and extensions
    """
    def __init__(self, path):
        stat = os.stat(path)
        for i in dir(stat):
            if i.startswith("st_"):
                setattr(self, i, getattr(stat, i))
        self.path = path
        self.name = os.path.basename(path)
        self.basename, self.ext = os.path.splitext(self.path)
        self.dir = os.path.dirname(path)
        n, sext = os.path.splitext(self.basename)
        self.part = None
        self.compression = None # for tar
        if sext.startswith(".part") and self.ext == ".rar":
            self.basename = n
            self.part = sext[5:].strip(". ")
        elif sext == ".tar":
            self.basename = n
            self.compression = self.ext.strip(". ")
            self.ext = ".tar"
        self.ext = self.ext.strip(". ")
        str.__init__(self, path)


class FilePluginManager(object):
    def __init__(self):
        self.plugins = []
        self.counter = 0
        self.pool = variablesizepool.VariableSizePool(1)
        self.group = Group()
        self.hddsem = Semaphore(2)
        self.threadpool = ThreadPool(2)
        
    def add(self, plugin):
        self.counter += 1
        try:
            prio = plugin.priority
        except AttributeError:
            prio = 100
        bisect.insort(self.plugins, (prio, self.counter, plugin))

    def process(self, fp, delete_after_processing=False):
        try:
            path, file = fileorpath(fp)
        except BaseException as e:
            if isinstance(fp, core.File):
                if not fp.enabled or fp.last_error:
                    return
                fp.fatal('failed opening file: {}'.format(e))
            log.error('failed opening file: {}'.format(e))
            return
        for _, _, plugin in self.plugins:
            if file and plugin.name in file.completed_plugins:
                continue
            #if globalconfig['download.state'] != 'started':
            #    return
            try:
                res = plugin.match(path, file)
                if res is None:
                    break
                if not res:
                    continue
            except:
                log.exception("error in plugin {}")
                continue
                
            if file:
                if file.state != plugin.name:
                    with transaction:
                        file.state = plugin.name
                complete = True
                try:
                    res = self.execute_plugin(plugin, path, file)
                    if res is False:
                        break
                    with transaction:
                        file.state = '{}_complete'.format(plugin.name)
                    event.fire('fileplugin:complete', path, file, plugin)
                except:
                    complete = False
                    raise
                finally:
                    if complete:
                        with transaction:
                            file.completed_plugins.add(plugin.name)
                if not file.enabled or file.last_error:
                    break
            else:
                self.execute_plugin(plugin, path, file)
        if delete_after_processing:
            try:
                os.unlink(path)
            except:
                pass
        e = 'fileplugin:done', path, file
        if isinstance(file, core.File):
            file.fire_after_greenlet(*e)
        else:
            event.fire(*e)

    def execute_plugin(self, plugin, path, file):
        f = plugin.process
        args = [path, file]
        kwargs = {arg: getattr(self, arg, None) for arg in f.func_code.co_varnames[2:f.func_code.co_argcount]}
        return f(*args, **kwargs)

    def dispatch(self, fp, delete_after_processing=False):
        if isinstance(fp, core.File):
            if fp.working:
                return
            fp.spawn(self.process, fp, delete_after_processing)
        else:
            gevent.spawn(self.process, fp, delete_after_processing)

def fileorpath(fp):
    try:
        path = fp.get_complete_file()
        file = fp
    except AttributeError:
        path = fp
        file = None
    return FilePath(path), file


@event.register('download:started')
def on_download_started(e):
    for f in core.files():
        if not f.working and f.package.system == 'download' and 'download' in f.completed_plugins:
            spawn_tasks(e, f, None)

@event.register('torrent:started')
def on_torrent_started(e):
    for f in core.files():
        if not f.working and f.package.system == 'torrent' and 'download' in f.completed_plugins:
            spawn_tasks(e, f, None)

@event.register('file.state:changed')
def spawn_tasks(e, file, old):
    core.sort_queue.wait()
    if file.last_error is not None:
        return
    if not file.enabled:
        return
    if file.file_plugins_complete:
        return
    if globalconfig['{}.state'.format(file.package.system)] != 'started':
        return
    if 'download' not in file.completed_plugins:
        return
    file.run_after_greenlet(manager.dispatch, file)


manager = FilePluginManager()

def init():
    for mod in plugintools.load("file"):
        manager.add(mod)
        
def startfile(path):
    try:
        return os.startfile(path)
    except AttributeError:
        if sys.platform == "darwin":
            tool = "open"
        else:
            tool = "xdg-open"
        try:
            return subprocess.call([tool, path])
        except:
            return False

if sys.platform.startswith("win") and "nose" not in sys.argv[0]:
    from win32com.shell import shell, shellcon

    def selectfiles(show):
        folders = defaultdict(list)
        for p in show:
            folders[os.path.dirname(p)].append(os.path.basename(p))
        for path, files in folders.iteritems():
            files = set(os.path.splitext(f)[0] for f in files) | set(files)
            folder = shell.SHILCreateFromPath(path, 0)[0]
            desktop = shell.SHGetDesktopFolder()
            shell_folder = desktop.BindToObject(folder, None, shell.IID_IShellFolder)
            shell.SHOpenFolderAndSelectItems(
                folder,
                [item for item in shell_folder if desktop.GetDisplayNameOf(item, 0) in files],
                0)
        return 0
        
elif sys.platform == "darwin":
    from AppKit import NSWorkspace, NSURL
    workspace = NSWorkspace.sharedWorkspace()

    def selectfiles(show):
        workspace.activateFileViewerSelectingURLs_(list(NSURL.fileURLWithPath_(i.decode("utf-8")) for i in show))
        return 0
else:
    def selectfiles(show): # xxx test for nautilus? gnome-open?
        parent = os.path.dirname(show[0])
        return startfile(parent)

@interface.register
class FileInterface(interface.Interface):
    name = "file"

    def process(path):
        manager.dispatch(path)

    def upload(data=None, name=None, encoding="base64"):
        #print "pre decode", len(data), name
        if encoding == "raw":
            pass
        elif encoding == "base64":
            data = base64.standard_b64decode(data)
        else:
            raise NotImplementedError("data encoding is not implemented")
        fd, path = tempfile.mkstemp(dir=core.config.download_dir, suffix=name)
        f = os.fdopen(fd, "wb")
        f.write(data)
        f.close()
        manager.dispatch(path, delete_after_processing=True)

    def add(path=None):
        manager.dispatch(path)

    def cmdline_add(path=None):
        manager.dispatch(path)
        webbrowser.open_new_tab(login.get_sso_url())
        
    def openfolder(path=None):
        if not path:
            return False
        if not os.path.isdir(path):
            return False

        return startfile(path)
        
    def select(packageids=None, fileids=None):
        show = []
        if not fileids:
            fileids = []
        if packageids:
            for p in core.packages():
                if p.id in packageids:
                    fileids.extend(f.id for f in p.files)
        for f in core.files():
            if f.id in fileids:
                path = f.get_complete_file()
                if os.path.exists(path):
                    show.append(path)
                    continue
                path = f.get_download_file()
                if os.path.exists(path):
                    show.append(path)
        return selectfiles(show)

    def force_extract(fileids=None):
        if not fileids:
            return
        for f in core.files():
            if f.id in fileids:
                path = f.get_complete_file()
                if os.path.exists(path):
                    path = FilePath(path)
                    pluginname = path.ext + "extract"
                    for _, __, plugin in manager.plugins:
                        if plugin.name == pluginname:
                            manager.execute_plugin(plugin, path, f)
                            break
