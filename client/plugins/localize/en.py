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

ID = 'en'
NAME = 'English'
FALLBACK = ['es', 'fr', 'de']
TEXT = dict()

# RPC

rpc__options = "RPC options"
rpc__usage = ' [RPC command 1] [RPC command 2] [...]'
rpc__epilog = """
You can call interface methods direct from commandline:
    download.am "module.command arg1=param1 -- arg2=param2" "mod.cmd arg1=param1 -- arg2=param2 -- ..."

Each argument is an interface call. The interface parameters are seperated by --
To get an overview over the available modules do:
    download.am "interface.list_modules"

As argument parameter you can pass json objects.
All results are json encoded.

When there is no running application instance the client will be started and the commands
are executed after initialisation.
You can use the argument --exit-after-exec to close the client after all commands are executed.

"""
rpc__exit_after_exec = 'close the application after all RPC commands are executed. Default behaivor when there is a currently running instance.'

# logger

logger__options = 'Log options'
logger__valid_levels = "Valid log levels are: DEBUG, INFO, WARNING, ERROR, CRITICAL"
logger__log_level = 'set the log level for console output (default: DEBUG)'
logger__log_file = 'set the log file. if FILE is "off" log will be disabled'
logger__log_file_level = 'set the log level of the logfile (default: DEBUG)'

# login

login__options = 'Login options'
login__username = 'login username'
login__password = 'login password'
login__save_password = 'save the password in config file so you don\'t have to enter this again'

# UI

ui__options = 'User interface (GUI) options'
ui__headless = 'use no interface. set login data as command line arguments'
ui__open_browser = 'open browser at startup (if config variable is set to true)'
ui__disable_splash = u"don't show splashscreen at start"

# loader

loader__usage = '%prog [options]'
loader__help = 'show this help message and exit'

# api

api__options = 'API options'
api__api_log = 'Log the API traffic'

# systray.win

systray__win__tooltip = """Download.am Client
{complete} / {total} transferred
{working} / {queued} files working at {speed}/s"""

systray__win__tooltip_idle = """Download.am Client
The download queue is empty"""

systray__win__tooltip_stopped = """Download.am Client
The download is currently stopped"""
# api

core__options = 'Download options'
core__shutdown = 'Shutdown computer when all downloads are complete'
