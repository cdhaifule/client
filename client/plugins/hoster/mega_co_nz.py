# -*- coding: utf-8 -*-
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

from __future__ import absolute_import
import time
from ... import hoster, download, ratelimit
from Crypto.Util import Counter
from Crypto.Cipher import AES
from ...contrib.mega import Mega, crypto

@hoster.host
class this:
    model = hoster.HttpHoster
    name = 'mega.co.nz'
    patterns = [
        hoster.Matcher('https?', '*.mega.co.nz'),
    ]
    can_resume = True

def on_check(file):
    mega = file.account.mega
    try:
        handle, key = mega._parse_url(file.url).split('!')
    except ValueError:
        file.no_download_link()
    file.set_infos(**mega.get_public_file_info(handle, key))
    
def get_download_context(file):
    file.set_download_context(
        account=this.get_account('download', file),
        can_resume=True,
        download_func=on_download,
        download_next_func=on_download_next)
    
def on_download(chunk):
    return MegaDownload(None)

def on_download_next(chunk, data):
    return on_download(chunk)


class ProgressMega(Mega):
    def download_chunk(self, processor, dest_path=None):
        path = self._parse_url(processor.chunk.url).split('!')
        file_handle = path[0]
        file_key = path[1]
        self.download_file(processor, file_handle, file_key, dest_path)
        
    def download_file(self, processor, file_handle=None, file_key=None, dest_path=None):
        _file_key = file_key
        file_key = crypto.base64_to_a32(file_key)
        file_data = self._api_request({'a': 'g', 'g': 1, 'p': file_handle})
        k = (file_key[0] ^ file_key[4], file_key[1] ^ file_key[5],
             file_key[2] ^ file_key[6], file_key[3] ^ file_key[7])
        iv = file_key[4:6] + (0, 0)
        file_url = file_data['g']
        file_size = file_data['s']
        resp = processor.chunk.account.get(file_url, chunk=processor.chunk, stream=True, set_exact_range=True)
        input_stream = resp.raw
        add_initial, dummy_size = divmod(processor.chunk.pos, 16)
        initital = (((iv[0] << 32) + iv[1]) << 64) + add_initial
        counter = Counter.new(128, initial_value=initital)
        kk = crypto.a32_to_str(k)
        aes = AES.new(kk, AES.MODE_CTR, counter=counter)
        if dummy_size:
            aes.decrypt("\0"*dummy_size)
        processor.chunk.file.set_infos(hash_type="cbc_mac_mega", hash_value=_file_key)
        for chunk_start, chunk_size in sorted(crypto.get_chunks(file_size)):
            chunk = input_stream.read(chunk_size)
            processor.last_read += len(chunk)
            t = time.time()
            chunk = aes.decrypt(chunk)
            processor.write(chunk)
            ratelimit.sleep(len(chunk), time.time()-t)

    def get_id_from_obj(self, node_data):
        for i in node_data['f']:
            if i["h"] and i["a"]:
                return i['h']
        return None

class MegaDownload(download.DownloadFunction):
    def read(self):
        return None
    
    def process(self):
        mega = self.chunk.account.mega
        mega.download_chunk(self)


def on_initialize_account(self):
    self.mega = ProgressMega()
