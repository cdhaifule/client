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

import sys
import os
import optparse

"""
kurzer test der patchfunktion
arg1 ist pfad zu einer alten testinstallation
arg2 patch ist der patch der drauf angewendet werden soll
"""

from client import settings
from client import patch

def optionparser():
    parser = optparse.OptionParser()
    parser.add_option("-p", "--platform", dest="platform", help="set platform repo", default="macos")
    parser.add_option("-b", "--branch", dest="branch", help="set branch", default="stable")
    parser.add_option("-s", "--server", dest="server", help="patchserver", default="http://patch.download.am")
    parser.add_option("-f", "--from", dest="file", help="use local patchfile", default=False)
    parser.add_option("-c", "--client", dest="client", help="path to app dir to be patched")
    parser.add_option("-r", "--rev", dest="rev", help="rev of client build to patch to current")
    parser.add_option("-t", "--rev2", dest="rev2", help="rev to patch to")
    return parser.parse_args()

def prep(options, args):
    settings.patchserver = options.server

    def external_rename(*args):
        print "app wants to restart extern?", args
    
    def restart_app():
        print "app wants to restart"
        
    def version():
        return options.rev
    
    patch.external_rename = external_rename
    patch.restart_app = restart_app
    sys.frozen = True
    settings.app_dir = patch.app_dir = options.client
    patch.platform = options.platform
    patch.version = version

def _test_patcher():
    """this is no automatic unit test function
    """
    options, args = optionparser()
    prep(options, args)
    if not options.file:
        from patch import create, git
        git.fetch(silent=False)
        filename = "/tmp/test.tar.bz2"
        if os.path.exists(filename):
            os.remove(filename)
        create.create(options.platform, options.rev, options.branch, filename)
    else:
        filename = options.file
    
    with open(filename) as f:
        patch.patch(f.read())
    
def main():
    _test_patcher()
        
if __name__ == '__main__':
    main()
