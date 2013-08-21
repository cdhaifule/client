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

import json
import bisect

from . import proto
from .. import scheme, logger
from ..config import globalconfig
from ..plugintools import Url

log = logger.get('api.push')

config = globalconfig.new('api').new('push')
config.default('interval', 0, float)

@config.register('inverval')
def _(value):
    listener.delay = value

class ApiListener(scheme.DelayedListener):
    def __init__(self):
        scheme.DelayedListener.__init__(self, 'api', config['interval'])

    def prepare(self, update):
        def rename(old, new):
            if old in data:
                data[new] = data[old]
                del data[old]

        push = []

        priorities = {
            'config': 1,
            'proxy': 2,
            'account': 3,
            'package': 4,
            'file': 5,
            'input': 6,
            'error': 7}
        for data in update:
            priority = data['table'] in priorities and priorities[data['table']] or 100, data['id'], len(push)

            if data['action'] == 'new':
                data['action'] = 'update'

            if data['table'] == 'config':
                del data['id']

            elif data['table'] == 'package':
                data['table'] = 'container'

            elif data['action'] != 'delete':
                if data['table'] == 'file':
                    rename('package', 'parent')
                    if 'substate' in data and data['substate'][0] == 'waiting_account':
                        data['substate'] = ['waiting', data['next_try']]
                    if 'completed_plugins' in data:
                        data['completed_plugins'] = list(data['completed_plugins'])
                
                elif data['table'] == 'account':
                    # don't send free dummy accounts
                    t = scheme.scheme.get_by_uuid(data['id'])
                    assert t._table_name == data['table']
                    if t._private_account:
                        continue
                    if 'expires' in data and data['expires']:
                        data['expires'] = int(data['expires']*1000)

                elif data['table'] == 'input':
                    try:
                        t = scheme.get_by_uuid(data['id'])
                    except KeyError:
                        continue
                    if t.ignore_api:
                        continue
                    if 'parent' in data and data['parent'] and data['parent'][0] == 'chunk':
                        t = scheme.get_by_uuid(data['parent'][1])
                        t = t.file
                        data['parent'] = [t._table_name, t.id]

                elif data['table'] == 'patch_source':
                    if 'url' in data and data['url']:
                        url = Url(data['url'])
                        if url.password is not None:
                            url.password = None
                        data['url'] = url.to_string()
                        if data['url'].startswith('http://repo.download.am/#'):
                            data['url'] = data['url'].replace('http://repo.download.am/#', 'http://github.com/downloadam/')
                    if 'config_url' in data:
                        if data['config_url'] is not None:
                            data['config_url'] = data['config_url'].replace('dlam-config.yaml', '')

                if data['table'] in ('account', 'package', 'file'):
                    rename('next_try', 'retry')

            bisect.insort(push, (priority, data))

        push = [p[1] for p in push]
        #self.dump(push)
        return push

    def on_commit(self, update):
        push = self.prepare(update.values())
        #log.debug('sending {} objects'.format(len(push)))
        """if len(push) > 15:
            self.delay = config['interval'] + 1.0
            log.debug('throttling to {}'.format(self.delay))
        else:
            self.delay = config['interval']"""
        proto.send('frontend', payload=push)

    def dump(self, update):
        print "ApiListener:"
        for tmp in update:
            data = {}
            data.update(tmp)
            print "--- {:6}: {:9} {:4}".format(data['action'], data['table'], 'id' in data and data['id'] or '-'),
            del data['action']
            del data['table']
            if 'id' in data:
                del data['id']
            print json.dumps(data, sort_keys=True)

listener = ApiListener()
