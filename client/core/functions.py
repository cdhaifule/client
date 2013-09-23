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

import gevent

from collections import defaultdict

from .engine import packages, files, convert_name, lock, log, config, Package, File
from .. import hoster, ui
from ..scheme import transaction
from types import GeneratorType

def add_links(links, package_name=None, extract_passwords=None, system='download', package_id=None, ignore_plugins=None):
    if isinstance(links, basestring):
        links = hoster.collect_links(links)
    elif type(links) not in (list, dict, set, GeneratorType):
        links = [links]

    if extract_passwords:
        if isinstance(extract_passwords, basestring):
            extract_passwords = [extract_passwords]
        elif not isinstance(extract_passwords, list):
            extract_passwords = list(extract_passwords)
    added = []
    set_infos = list()
    default_package = None

    with lock:
        url_index = defaultdict(set)
        for f in files():
            url_index[f.url].add(f)

        if package_name is not None:
            package_name = convert_name(system, package_name)

        hosts = set()
        
        with transaction:
            for link in links:
                if not isinstance(link, dict):
                    link = {'url': link}

                # get plugin
                if 'host' not in link:
                    try:
                        host = hoster.find(link['url'])
                    except ValueError:
                        host = None
                    if not host:
                        log.warning('found no module for url {}'.format(link['url']))
                        continue
                    if ignore_plugins and host[0].name in ignore_plugins:
                        log.debug('ignored url {} as requested'.format(link['url']))
                        continue
                    link['host'], link['pmatch'] = host

                # normalize url
                link['url'] = link['host'].normalize_url(link['url'], link['pmatch'])
                if not link['url']:
                    log.warning('{}.normalize({}) returned null'.format(link['host'].name, link['url']))
                    continue

                # set package infos
                link['extract_passwords'] = extract_passwords

                # check if file already exists
                dupes = url_index[link['url']]
                if dupes:
                    if any(True for f in dupes if f.state == 'check' and f.last_error == 'link already exists' and f.enabled is False):
                        continue

                    if any(True for f in dupes if f.state in ('check', 'collect')):
                        log.warning(u'duplicate link: {url}'.format(**link))
                        continue

                #find package by package_name or create a new one
                if default_package is None:
                    package = filter(lambda p: p.state == 'collect' and p.name == package_name and p.system == system and (package_id is None or package_id == p.id), packages())
                    if package:
                        package = package[0]
                        if extract_passwords:
                            for password in extract_passwords:
                                if password not in package.extract_passwords:
                                    package.extract_passwords.append(password)
                        default_package = package
                if default_package is None:
                    package = Package(id=package_id, name=package_name, extract_passwords=extract_passwords, system=system)
                    default_package = package
                else:
                    package = default_package
                link['package'] = package

                if dupes:
                    dupe = dupes.pop()
                    dupes.add(dupe)
                    log.debug(u'already downloading: {url}'.format(**link))
                    link['size'] = dupe.size
                    link['approx_size'] = dupe.approx_size
                    link['name'] = dupe.name
                    link['enabled'] = False
                    link['state'] = 'check'
                    link['last_error'] = 'link already exists'
                    link['last_error_type'] = 'info'
                    file = File(**link)
                    url_index[file.url].add(file)

                    from .. import check
                    check.assign_file(file, dupe.package.name)
                    added.append(file.id)
                    continue

                log.debug(u'new link: {url}'.format(**link))

                if link.get('name'):
                    name = link['name']
                    del link['name']
                else:
                    name = None

                file = File(**link)
                url_index[file.url].add(file)

                if name:
                    with transaction:
                        set_infos.append((file, dict(name=name)))
                hosts.add(file.host)
                added.append(file.id)

            for i in set_infos:
                i[0].set_infos(**i[1])
                if i[0].package.system != 'torrent':
                    i[0].state = 'check'

        for host in hosts:
            gevent.spawn(file.host.get_account, 'download', file)

        if added and config.open_browser_after_add_links:
            ui.browser_to_focus(True)

        return added


def accept_collected(file_filter=None, **filter):
    """TODO: accept also completly unchecked links? create a extra package for them?"""
    for package in packages():
        with transaction:
            if not package.state in ('check', 'collect'):
                continue
            if not package.match_filter(**filter):
                continue

            updated = list()

            if package.system == 'torrent':
                target_package = package
                for file in package.files:
                    if file.host.can_download(file):
                        file.state = 'download'
                        updated.append(file)
            else:
                for file in package.files[:]:
                    if file_filter and not file.match_filter(**file_filter):
                        continue
                    if not file.host.can_download(file):
                        continue
                    if file.last_error:
                        file.log.debug('deleting, cause: last_error = {}'.format(file.last_error))
                        file.delete()
                        continue
                    if not file.enabled:
                        file.log.debug('deleting, cause: not enabled')
                        file.delete()
                        continue
                    file.state = 'download'
                    updated.append(file)

            if not updated:
                continue

            if len(updated) == len(package.files):
                target_package = package
            else:
                for p in packages():
                    if p.state == 'download' and p.name == package.name and p.system == package.system:
                        target_package = p
                        break
                else:
                    target_package = package.clone_empty(state='download')
                for file in updated:
                    file.package = target_package
            
            target_package.enabled = True
            target_package.state = 'download'


def url_exists(url):
    host = hoster.find(url)
    if not host:
        log.warning('found no module for url {}'.format(url))
        return False

    url = host[0].normalize_url(url, host[1])
    for f in files():
        if f.url == url:
            return True
    
    return False
