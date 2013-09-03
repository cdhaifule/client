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

import gevent

from .. import host, util

@host
class this:
    ### global hoster variables
    name = None

    global_max_check_tasks = 20
    global_max_download_tasks = 20
    
    ### account settings (by account instance)

    max_check_tasks_free = 20
    max_download_tasks_free = 1
    max_chunks_free = 1

    max_check_tasks_premium = 20
    max_download_tasks_premium = 20
    max_chunks_premium = None

    can_resume_free = False
    can_resume_premium = True
    
    max_filesize_free = None
    max_filesize_premium = None

    # preferences used to calculate plugin weight
    has_captcha_free = None
    max_download_speed_free = None
    waiting_time_free = None

    has_captcha_premium = None
    max_download_speed_premium = None
    waiting_time_premium = None

def can_download(file):
    return True

def get_download_context(file, account=None):
    account = account or this.get_account('download', file)
    file.set_download_context(account=account)

    premium = file.account.premium
    if premium:
        if file.account.is_expired:
            #recheck and reassign account
            file.account.reset()
            premium = False
        elif not file.account.has_traffic:
            file.log.warning('Der Premium Account hat nicht mehr genug Traffic für den Download. Datei wird im Free-Modus geladen')
            premium = False

    if premium:
        file.set_download_context(
            download_func=this.on_download_premium,
            download_next_func=this.on_download_premium_next,
            can_resume=this.can_resume_premium)
    else:
        print "try to use multiaccount", this.name, file.retry_num
        if file.retry_num == 0:
            # check if we have a multi hoster premium account
            def multi_match(acc, hostname):
                print "multi_match:", hostname, this.name
                print acc.compatible_plugins
                if not this.name in acc.compatible_plugins:
                    return False
                return True

            acc = util.get_multihoster_account('download', multi_match, file)
            print "acc is:", acc
            if acc:
                try:
                    file.log.info('trying multihoster {}'.format(acc.name))
                    acc.hoster.get_download_context(file, account=acc)
                    return
                except gevent.GreenletExit:
                    if not file.next_try and file.last_error:
                        file.retry(file.last_error, 1)
                    raise
                except BaseException as e:
                    this.log.exception(e)

        # found no multi plugin. continue the normal way
        file.set_download_context(
            download_func=this.on_download_free,
            download_next_func=this.on_download_free_next,
            can_resume=this.can_resume_free)

def on_download_free(chunk):
    raise NotImplementedError()

def on_download_premium(chunk, url=None):
    raise NotImplementedError()
    
def on_download_free_next(chunk, data):
    return this.on_download_next(chunk, data)

def on_download_premium_next(chunk, data):
    return this.on_download_next(chunk, data)
