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

import gevent

from bottle import GeventServer, Bottle, request, response

xxx = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"

size_mb = 10

def yield_data(start_at=None):
    pos = 0
    for i in xrange(1024):
        for j in xrange(size_mb):
            data = xxx[(i + j) % len(xxx)]*1024
            if start_at:
                if pos + len(data) > start_at:
                    data = data[start_at - pos:]
                    start_at = None
            pos += len(data)
            if start_at:
                continue
            yield data

#import hashlib
#m = hashlib.md5()
#for x in yield_data():
#    m.update(x)
#print m.hexdigest()
#exit(1)

def _default(func=yield_data, name='{}mb.bin'.format(size_mb)):
    response.set_header('Content-Disposition', 'filename="{}"'.format(name))
    response.set_header('Content-Length', size_mb*1024**2)
    return func()

def _resume(func=yield_data, name='{}mb.bin'.format(size_mb)):
    start_at = None

    env = request.headers.__dict__['environ']
    if 'HTTP_RANGE' in env:
        http_range = env['HTTP_RANGE'].replace('bytes=', '').split('-', 1)
        start_at = int(http_range[0])
        #end_at = http_range[1] and int(http_range[1]) or None

    length = size_mb*1024**2
    if start_at:
        response.set_header('Content-Range', 'bytes {}-{}/{}'.format(start_at, length, length))
        length -= start_at
        response.status = 206

    response.set_header('Content-Disposition', 'filename="{}"'.format(name))
    response.set_header('Content-Length', length)
    response.set_header('Accept-Ranges', 'bytes')

    return func(start_at)

def _resume_fake(func=yield_data):
    start_at = None

    env = request.headers.__dict__['environ']
    if 'HTTP_RANGE' in env:
        http_range = env['HTTP_RANGE'].replace('bytes=', '').split('-', 1)
        start_at = int(http_range[0])
        if start_at > 0:
            response.status = 416
            return response.status_line

    length = size_mb*1024**2
    response.set_header('Content-Disposition', 'filename="{}mb.bin"'.format(size_mb))
    response.set_header('Content-Length', length)
    response.set_header('Accept-Ranges', 'bytes')

    return func(start_at)

def _noresume(func=yield_data):
    env = request.headers.__dict__['environ']
    if 'HTTP_RANGE' in env:
        http_range = env['HTTP_RANGE'].replace('bytes=', '').split('-', 1)
        start_at = int(http_range[0])
        #end_at = http_range[1] and int(http_range[1]) or None
        if start_at > 0:
            response.status = 416
            return response.status_line
    
    length = size_mb*1024**2

    response.set_header('Content-Disposition', 'filename="{}mb.bin"'.format(size_mb))
    response.set_header('Content-Length', length)

    return func()


http = Bottle(catchall=False)

@http.route("/{}mb.bin".format(size_mb))
def route_default():
    return _default()

@http.route("/resume/{}mb.bin".format(size_mb))
def route_resume():
    return _resume()

@http.route("/resume_fake/{}mb.bin".format(size_mb))
def route_resume_fake():
    return _resume_fake()

@http.route("/noresume/{}mb.bin".format(size_mb))
def route_noresume():
    return _noresume()

limit = dict()

@http.route("/<resume>/connection_limit/{}mb.bin".format(size_mb))
def route_resume_connection_limit(resume):
    if resume == 'resume':
        func = _resume
    elif resume == 'noresume':
        func = _noresume
    elif resume == 'resume_fake':
        func = _resume_fake
    else:
        response.status = 404
        return

    if not request.environ['REMOTE_ADDR'] in limit:
        limit[request.environ['REMOTE_ADDR']] = 0
    if limit[request.environ['REMOTE_ADDR']] >= 2:
        response.status = 503
        return

    limit[request.environ['REMOTE_ADDR']] += 1
    try:
        for data in func():
            yield data
    finally:
        limit[request.environ['REMOTE_ADDR']] -= 1

@http.route("/anyname/<:re:(.*/)?><name>")
def route_anyname(name):
    return _resume(name=name)


########## patch repo tests

@http.route('/repotest/redir/config')
def repotest_redir_config():
    response.status = 302
    response.headers['Location'] = '/repotest/redir/config2'

@http.route('/repotest/redir/config2')
def repotest_redir_config2():
    response.status = 302
    response.headers['Location'] = '/repotest/config'

@http.route('/repotest/config')
def repotest_config():
    response.status = 200
    return 'hoster: http://github.com/downloadam/hoster.git'

@http.route('/repotest/redir/git')
def repotest_redir_git():
    response.status = 302
    response.headers['Location'] = '/repotest/git'

@http.route('/repotest/git')
def repotest_git():
    response.status = 302
    response.headers['Location'] = 'http://github.com/downloadam/hoster.git'


host = '127.0.0.1'
port = 4567
url = 'http://{}:{}'.format(host, port)

md5_10mb = '9a978dd12f07769d9b4cc77b1d38de9c'
md5_100mb = '2f09f876168be1d4776b3ef43e6f01d1'

server = None
server_greenlet = None

def start():
    global server
    global server_greenlet

    if not server:
        server = GeventServer(host, port)
        server_greenlet = gevent.spawn(server.run, http)

def stop():
    global server
    global server_greenlet

    server = None
    server_greenlet.kill()
    server_greenlet = None
