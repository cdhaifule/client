#!/usr/bin/env python
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
import base64

from .. import host, util

@host
class this:
    ### global hoster variables
    
    # name of the plugin
    name = None
    # list of alias names
    alias = list()
    # priority (should be always 100, except on special plugins)
    priority = 100
    # `Matcher` or `re.complie` patterns
    patterns = list()
    # config definition (see plugin configs for more details)
    config = None

    # use the remote link check cache (very fast when links are known)
    use_check_cache = True

    # global limits for this plugin
    global_max_check_tasks = 20
    global_max_download_tasks = 20

    # search definition (see plugin search howto for more details)
    search = None
    # url template. the url is formatted with url_template.format(**pattern_match)
    url_template = None

    # url to favicon when it's not on the default url
    favicon_url = None
    # base64 encoded favicon image
    favicon_data = None
    
    ### account settings (by account instance)

    # limits for a account instance
    max_check_tasks = 20
    max_download_tasks = 20

    # preferences of account instance
    max_chunks = None
    can_resume = True
    max_filesize = None

    # automatically sets a user agent
    set_user_agent = False

    # preferences used to calculate plugin weight
    # is there a captcha before the download can start?
    # True = lowest priority, None = medium priority, False = hight priority
    has_captcha = None
    # approx maximal download speed of this plugin in 50 kbs steps -> int(max_download_speed/50)
    max_download_speed = None
    # approx wait time in minutes before the download can start
    waiting_time = None

def can_download(file):
    """function that is called before the files are moved from linkcollector to download.
    useful to hole back some files.
    """
    return True

ignore_icons = dict()

def load_icon(hostname):
    name = os.path.splitext(this.module.__file__)[0]+'.ico'
    if os.path.exists(name):
        try:
            with open(name, 'rb') as f:
                return f.read()
        except:
            pass
    else:
        from ...patch import get_file_iterator
        print this.module.__name__
        try:
            source, name = this.module.__name__.rsplit('.', 1)[1].split('_', 2)[1:]
        except ValueError:
            pass
        else:
            path = os.path.join('hoster', name+'.ico')
            try:
                files = list(get_file_iterator(source, path))
                assert len(files) == 1
            except:
                pass
            else:
                return files[0].get_contents()
    print "default load_icon this is:", this.name, this.favicon_url
    if this.favicon_data:
        data = base64.b64decode(this.favicon_data)
    else:
        if this.favicon_url:
            data = util.find_favicon(url=this.favicon_url)
        else:
            data = util.find_favicon(hostname)

    return data

def get_hostname(file=None):
    return this.name

def normalize_url(url, pmatch):
    if pmatch.matcher.template is not None:
        return pmatch.matcher.template.format(**pmatch)
    if this.url_template is not None:
        return this.url_template.format(**pmatch)
    return url

def on_check(file):
    """possible return values:
    None - file has no postprocessing
    list - core.add_links(result) is called
    tuple - core.add_links(*result) is called
    dict - core.add_links(**result) is called
    when return value is not None and file.name, file.get_any_size() and file.last_error is None the file is deleted after greenlet (file.delete_after_greenlet)"""
    util.check_download_url(file, file.url)

def get_download_context(file):
    """
    have to set file.download_func, file.download_next_func
    normally also sets file.can_resume and file.max_chunks
    """
    file.set_download_context(
        account=this.get_account('download', file),
        download_func=this.on_download,
        download_next_func=this.on_download_next)

def on_download(chunk):
    """overwrite
    with Http modification return value can be:
        context
        url[, context]
        response[, context]
        stream[, context]
    on all other objects it has to return a Context object
    """
    return chunk.account.get(chunk.url, chunk=chunk, stream=True)

def on_download_next(chunk, data):
    """overwrite
    initialize the download for the next chunk
    have to return file-like object or None if no more chunks are possible
    """
    raise NotImplementedError()
    
def handle_download_result(chunk, data):
    """overwrite
    tool function to add hooks to on_download return value
    have to return:
    stream[, data_for_download_next]
    eg. resp.raw, resp.url
    or: resp.raw, None
    the first parameter must be a stream-like object, the seconds one will
    be passed to the on_download_next function.
    """
    if isinstance(data, list) or isinstance(data, tuple) or isinstance(data, set):
        next = data[1:]
        return data[0], len(next) == 1 and next[0] or next
    return data, None
    
def on_initialize_account(account):
    pass

def weight(file):
    account = this.get_account('download', file) # assume download task (this is dirty)
    return (this.priority, account and account.weight or 0)
