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
import sys

from ... import hoster, plugintools


@hoster.host
class this:
    model = hoster.Hoster
    name = "file"
    priority = 150
    patterns = [
        hoster.Matcher('file'),
    ]
    favicon_url = "http://download.am/assets/img/icons/http/128icon.png"
    max_chunks = 1


@plugintools.filesystemencoding
def get_path(url):
    return url[7:]


def on_check(file):
    dirs = (file.get_extract_path(), file.get_complete_path(), file.get_download_path())
    path = get_path(file.url)

    print "file plugin, path is", repr(path)
    
    try:
        if file.size:
            size = file.size
        else:
            size = os.path.getsize(path)
    except OSError:
        file.set_offline()

    if not file.name:
        for p in dirs:
            if path.startswith(p):
                file.set_infos(name=path[len(p):].encode(sys.getfilesystemencoding()), size=size)
                return
        file.set_infos(name=os.path.basename(path), size=size)

    with hoster.transaction:
        file.state = "download_complete"
