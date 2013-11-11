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
import gevent

from ... import core, fileplugin
from ...container import container
from ... import input, logger

name = 'container'

config = fileplugin.config.new(name)
config.default('process_after_download', True, bool)
config.default('delete_after_process', None, bool)

log = logger.get("file.container")


def match(path, file):
    if file is not None and not config.process_after_download:
        return False
    return path.ext in container

    
def process(path, file, hddsem):
    with open(path) as f:
        links = container[path.ext](f.read())
    core.add_links(links)

    if config.delete_after_process is not None:
        delete = config.delete_after_process
    else:
        result = dict()
        delete = False
        elements = [
            input.Text('Added links of container {}.\nDelete container now?'.format(os.path.basename(path))),
            input.Input('remember', 'checkbox', label='Remember decision?'),
            input.Choice('delete', choices=[
                {"value": True, "content": "Yes"},
                {"value": False, "content": "No"},
            ])]
        try:
            result = input.get(elements, type='delete_container', parent=file, timeout=120)
            delete = result['delete']
        except input.InputTimeout:
            log.warning('input timed out')
        except input.InputError:
            log.exception('input was aborted')

        if result.get('remember', False):
            config.delete_after_process = delete

    if delete:
        if file:
            file.erase_after_greenlet()
        else:
            os.remove(path)
        raise gevent.GreenletExit()
