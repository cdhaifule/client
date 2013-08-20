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
import random

from .. import input

def solve(browser, challenge_id, timeout=60, parent=None):
    resp = browser.get("http://api.adscaptcha.com/Get.aspx?{}".format(challenge_id))

    challenge = re.search("challenge: '(.*?)',", resp.text).group(1)
    server = re.search("server: '(.*?)',", resp.text).group(1)

    resp = browser.get("{}Challenge.aspx".format(server), params={"cid": challenge, "dummy": random.random()})
    result = input.captcha_text(resp.content, 'image/jpeg', timeout=timeout, parent=parent)

    return result, challenge
