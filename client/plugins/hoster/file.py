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

from ... import hoster

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

def on_check(file):
    dirs = (file.get_extract_path(), file.get_complete_path(), file.get_download_path())
    path = file.url[7:]
    
    if not file.name:
        for p in dirs:
            if path.startswith(p):
                file.set_infos(name=path[len(p):])
                return
        file.set_infos(name=os.path.basename(path))

    if any(path.startswith(p) for p in dirs):
        with hoster.transaction:
            file.state = "download_done"

def on_download(chunk):
    print "on_download file"
