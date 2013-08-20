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

import loader
loader.init()

import requests

from client import interface, scheme, plugintools, api, debugtools

def test_proxy():
    return
    #ssh -v -N -D 127.0.0.1:1080 test.domain.com

    api.init()

    listener = scheme.PassiveListener('api')
    scheme.register(listener)

    type, host, port = 'socks5', '127.0.0.1', 1080

    def check_output(text):
        wc = plugintools.wildcard('<html><head><title>Current IP Check</title></head><body>Current IP Address: *</body></html>')
        return wc.match(text)

    resp = requests.get('http://checkip.dyndns.org/')
    proxyless_text = resp.text.strip()
    assert check_output(proxyless_text)

    interface.call('proxy', 'set', type=type, host=host, port=port)
    data = listener.pop().values()[0]
    debugtools.compare_dict(data, {'proxy.port': 1080, 'proxy.enabled': True, 'proxy.type': 'socks5', 'proxy.host': '127.0.0.1', 'action': 'update', 'table': 'config'})

    resp = requests.get('http://checkip.dyndns.org/')
    proxy_text = resp.text.strip()
    assert check_output(proxy_text)
    assert proxy_text != proxyless_text

    interface.call('proxy', 'remove')
    data = listener.pop().values()[0]
    debugtools.compare_dict(data, {'proxy.port': None, 'proxy.enabled': None, 'proxy.type': None, 'proxy.host': None, 'action': 'update', 'table': 'config'})

    resp = requests.get('http://checkip.dyndns.org/')
    proxyless_text2 = resp.text.strip()
    assert check_output(proxyless_text2)
    assert proxy_text != proxyless_text2
    assert proxyless_text == proxyless_text2

if __name__ == '__main__':
    test_proxy()
