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

import re

from .. import input

def parse(text):
    m = re.search(r'Recaptcha\.create\s*\(\s*["\']([^"\']+)', text)
    if not m:
        m = re.search(r'/recaptcha/api/challenge\?k=([^"\']+)', text)
        if not m:
            m = re.search(r'http://api\.recaptcha\.net/challenge\?k=([^"\'\s]+)', text)
    return m and m.group(1).split("&", 1)[0] or None

def solve(browser, challenge_id, timeout=60, parent=None):
    resp = browser.get('http://www.google.com/recaptcha/api/challenge', params={'k': challenge_id})
    try:
        server = re.search("server\s*:\s*'(.*?)',", resp.text).group(1)
        challenge = re.search("challenge\s*:\s*'(.*?)',", resp.text).group(1)
    except:
        raise ValueError('error reading infos')

    resp = browser.get("%simage" % server, params={'c': challenge})
    result = input.captcha_text(resp.content, 'image/jpeg', timeout=timeout, parent=parent)

    return result, challenge
