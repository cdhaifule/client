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

from .engine import config, log, lock, _packages, packages, files, Package, File, Chunk, GlobalStatus, global_status
from .functions import add_links, accept_collected, url_exists
from .events import sort_queue
from .. import db, interface
from ..plugintools import dict_json
from ..scheme import transaction, filter_objects_callback, get_by_uuid
from ..localize import _T


########################## init

def init_optparser(parser, OptionGroup):
    group = OptionGroup(parser, _T.core__options)
    group.add_option('--shutdown', dest="shutdown", action="store_true", default=False, help=_T.core__shutdown)
    parser.add_option_group(group)


def init(options):
    if not os.path.exists(config.download_dir):
        try:
            os.makedirs(config.download_dir)
        except BaseException as e:
            log.warning('error creating download directory {}: {}'.format(config.download_dir, e))

    db.unregister_listener()
    try:
        with transaction, db.Cursor() as cursor:
            yield 'loading packages'
            ai, bi, ci = 0, 0, 0
            aa = cursor.execute("SELECT * FROM package ORDER BY position")
            for a in aa.fetchall():
                try:
                    dict_json(a)
                    Package(**a)
                except RuntimeError:
                    pass
                except:
                    log.unhandled_exception('load package')

            yield 'loading files'
            bb = cursor.execute("SELECT * FROM file")
            for b in bb.fetchall():
                try:
                    dict_json(b)
                    f = File(**b)
                except (RuntimeError, TypeError):
                    pass
                except:
                    log.unhandled_exception('load file')
                else:
                    download = f.get_download_file()
                    complete = f.get_complete_file()
                    if "download" in f.completed_plugins and os.path.exists(complete) and os.path.exists(download):
                        try:
                            os.remove(download)
                        except (OSError, IOError) as e:
                            f.log.warning("could not remove file {}: {}".format(download, e))

            yield 'loading chunks'
            cc = cursor.execute("SELECT * FROM chunk")
            for c in cc.fetchall():
                try:
                    dict_json(c)
                    Chunk(**c)
                except RuntimeError:
                    pass
                except:
                    log.unhandled_exception('load chunk')
    finally:
        db.register_listener()

    ignore = set()
    for file in files():
        if file.host not in ignore:
            gevent.spawn(file.host.get_account, 'download', file)
            ignore.add(file.host)

    config.shutdown = bool(options.shutdown)


########################## interface

@interface.register
class Interface(interface.Interface):
    name = 'core'

    def printr(filter_file=None, not_filter_file=None):
        from .. import download
        print "-------------------------------------------------------------", download.config.state
        for package in packages():
            print package.position, package.id, package.state, package.name, package.last_error
            for file in package.files:
                if filter_file and not file.match_filter(None, **filter_file):
                    continue
                if not_filter_file and file.match_filter(None, **not_filter_file):
                    continue
                print "    ", file.enabled, file.working, file.id, file.state, repr(file.name), file.progress, file.last_error, 'reconnect:'+str(file.need_reconnect), file.get_any_size(), file.get_column_value('substate'), file.get_column_value('progress')
                #for chunk in file.chunks:
                #    print "        ", chunk.id, chunk.working, chunk.state, chunk, chunk.last_error, chunk.substate
        print "-------------------------------------------------------------"

    def add_links(links=None, package_name=None, extract_passwords=None, ignore_plugins=[]):
        """
        links:
            ["link1", "link2", ...]
            or
            [{"url": "http://test.com/foo1.rar"}, {"url": "http://test.com/foo2.rar"}, ...]
            or
            [{"url": "http://test.com/foo1.rar", "name": "foo1.rar}, {"url": "http://test.com/foo2.rar", "name": "foo2.rar}, ...]
            package_name and extract_passwords are optional
            extract_passwords is a list of possible extract passwords
        """
        return add_links(links, package_name, extract_passwords, ignore_plugins=ignore_plugins)
        
    def accept_collected(file_filter=None, **filter):
        """accepts collected packages
            all arguments: filter argument
                id=123
                name=foo.rar, size=123
                id=[123, 456, 789, ...]
                ...

            all api values are possible.
            offline, disabled or failed links are deleted
        """
        return accept_collected(file_filter=file_filter, **filter)

    def stop_package(**filter):
        """stops packages. for arguments see accept_collected"""
        with transaction:
            for obj in packages():
                if obj.match_filter('api', **filter):
                    obj.stop()
                    for f in obj.files:
                        f.enabled = False

    def start_file(**filter):
        """open a file. With MacOS use VLC by default, windows will use startfile only for now. other oses will use xdg-open"""
        filter_objects_callback(files(), filter, lambda obj: obj.startfile())

    def stop_file(**filter):
        """stops files. for arguments see accept_collected"""
        with transaction:
            for obj in files():
                if obj.match_filter('api', **filter):
                    obj.stop()
                    obj.enabled = False

    def delete_package(**filter):
        """deletes packages. for arguments see accept_collected"""
        with lock:
            with transaction:
                filter_objects_callback(_packages[:], filter, lambda obj: obj.delete())

    def delete_file(**filter):
        """deletes files. for arguments see accept_collected"""
        with lock:
            with transaction:
                for package in _packages[:]:
                    filter_objects_callback(package.files[:], filter, lambda obj: obj.delete())

    def erase_package(**filter):
        """erases packages. for arguments see accept_collected"""
        with lock:
            with transaction:
                filter_objects_callback(_packages[:], filter, lambda obj: obj.erase())

    def erase_file(**filter):
        """erases files. for arguments see accept_collected"""
        with lock:
            with transaction:
                for package in _packages[:]:
                    filter_objects_callback(package.files[:], filter, lambda obj: obj.erase())

    def modify_package(update=None, **filter):
        """modifies packages
            update:
                [
                    {"key": "new value"},
                    {"name": "new stupid name"}
                ]
            for keys you can change press Ctrl+F and search for read_only=False
            for all other arguments see accept_collected"""
        with transaction:
            if 'name' in update:
                def rename_package(package):
                    if package.state == 'collect' and package.name != update['name']:
                        for p in packages():
                            if p != package and p.state == 'collect' and p.name == update['name']:
                                for file in package.files[:]:
                                    file.package = p
                                return
                    package.modify_table(update)
                func = rename_package
            else:
                func = lambda obj: obj.modify_table(update)
            filter_objects_callback(packages(), filter, func)

    def modify_file(update=None, **filter):
        with transaction:
            filter_objects_callback(files(), filter, lambda obj: obj.modify_table(update))

    def move_file(target=None, **filter):
        target = get_by_uuid(int(target))
        assert isinstance(target, Package)

        def update(file):
            file.package = target
        with transaction:
            for p in _packages[:]:
                filter_objects_callback(p.files[:], filter, update)

    def activate_package(**filter):
        with transaction:
            filter_objects_callback(packages(), filter, lambda obj: obj.activate())

    def activate_file(**filter):
        with transaction:
            filter_objects_callback(files(), filter, lambda obj: obj.activate())

    def deactivate_package(**filter):
        with transaction:
            filter_objects_callback(packages(), filter, lambda obj: obj.deactivate())

    def deactivate_file(**filter):
        with transaction:
            filter_objects_callback(files(), filter, lambda obj: obj.deactivate())

    def reset_package(**filter):
        with transaction:
            filter_objects_callback(packages(), filter, lambda obj: obj.reset())

    def reset_file(**filter):
        with transaction:
            filter_objects_callback(files(), filter, lambda obj: obj.reset())

    def start():
        with transaction:
            interface.call('download', 'start')
            interface.call('torrent', 'start')
        
    def pause():
        with transaction:
            interface.call('download', 'pause')
            interface.call('torrent', 'pause')

    def stop():
        with transaction:
            interface.call('download', 'stop')
            interface.call('torrent', 'stop')

    def url_exists(id=None, urls=None):
        results = list()
        for url in urls:
            results.append(url_exists(url) and 1 or 0)
        return dict(id=id, results=results)
        
    def create_wdl(package_ids=None, file_ids=None, ident=None):
        files = list()
        for p in packages():
            if not p.id in package_ids:
                continue
            for f in p.files:
                if f.package.id in package_ids or f.id in file_ids:
                    files.append(dict(name=f.name, size=f.get_any_size(), url=f.url))
            return dict(files=files, ident=ident)

    def split_by(key=None, **filter):
        filter['system'] = 'download'
        filter_objects_callback(_packages[:], filter, lambda obj: obj.split(key))

_pyflakes_silence = [config, log, lock, _packages, packages, files, Package, File, Chunk, GlobalStatus, global_status, sort_queue]
