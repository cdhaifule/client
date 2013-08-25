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

import loader
loader.init()

import gevent
import requests

from client import interface, event
from client.captcha import recaptcha

def send_input(e, input):
    gevent.spawn_later(0.1, interface.call, 'input', 'answer', id=input.id, answer={'captcha': 'invalid test recaptcha answer'})

def test_recaptcha():
    browser = requests.session()
    resp = browser.get("http://www.google.com/recaptcha/demo/")
    challenge_id = recaptcha.parse(resp.text)

    result, challenge = recaptcha.solve(browser, challenge_id)

    data = {"recaptcha_challenge_field": challenge, "recaptcha_response_field": result}
    resp = browser.post("http://www.google.com/recaptcha/demo/", data=data)

    try:
        assert "Correct" in resp.text or "Incorrect" in resp.text or "Richtig" in resp.text or "Falsch" in resp.text or "Rangt." in resp.text or u"Rétt!" in resp.text or u"Feil." in resp.text or u"Fel." in resp.text
    except:
        print resp.text
        raise

test_recaptcha.setUp = lambda: event.add("input:request", send_input)
test_recaptcha.tearDown = lambda: event.remove('input:request', send_input)

if __name__ == '__main__':
    test_recaptcha()
