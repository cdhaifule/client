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

import time
import gevent

from client import interface, input, event

step = None

def fake_msg_reset(input):
    interface.call('input', 'reset_timeout', id=input.id, timeout=3)
    assert not input.timeout is None

def send_input(e, input):
    assert input.timeout > time.time()
    if step == "input":
        gevent.spawn_later(0.1, fake_msg_reset, input)
        gevent.spawn_later(0.2, interface.call, 'input', 'answer', id=input.id, answer=dict(captcha="invalid test answer"))
    elif step == "timeout":
        pass

def test_input():
    global step
    
    step = "input"
    text = input.captcha("bildatenlol", "image/jpeg", timeout=0.15)
    print text
    assert text == "invalid test answer"

    step = "timeout"
    try:
        text = input.captcha("bildatenlol", "image/jpeg", timeout=0.1)
        print text
        raise ValueError('InputTimeout exception expected')
    except input.InputTimeout:
        pass

test_input.setUp = lambda: event.add("input:request", send_input)
test_input.tearDown = lambda: event.remove('input:request', send_input)

if __name__ == '__main__':
    test_input()
