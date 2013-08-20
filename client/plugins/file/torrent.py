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

from ... import event, fileplugin, torrent, logger

name = 'torrent'
priority = 100

config = fileplugin.config.new(name)
config.default('process_after_download', True, bool)

log = logger.get('plugins.file.torrent')

def match(path, file):
    if file is not None and not config.process_after_download:
        return False
    if path.ext in ["torrent"]:
        return True
    return False
        
def process(path, file, hddsem, threadpool):
    p = file.get_complete_file() if file else path
    with open(p, 'rb') as f:
        data = f.read()

    torrent.add_torrent(data, file)
