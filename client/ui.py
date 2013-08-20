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
import sys
import gevent
import webbrowser

from . import logger, event
from .config import globalconfig
from .localize import _T

config = globalconfig.new('ui')
config.default('open_browser', True, bool, description="Open browser after client start")

log = logger.get('ui')

ui = None
splash = None
open_browser = True

def _headless():
    class Headless(object):
        has_ui = False
    return Headless(), 'headless'

def _cocoa():
    from .plugins.ui import cocoa as ui
    return ui, 'cocoa'

def _tk():
    from .plugins.ui import tk as ui
    return ui, 'Tk'

def init_optparser(parser, OptionGroup):
    group = OptionGroup(parser, _T.ui__options)
    group.add_option('--headless', action="store_true", default=False, help=_T.ui__headless)
    group.add_option('--no-browser', dest="open_browser", action="store_false", default=True, help=_T.ui__open_browser)
    group.add_option('--disable-splash', dest="disable_splash", action="store_true", default=False, help=_T.ui__disable_splash)
    parser.add_option_group(group)

def init(options=None):
    global ui
    global splash
    global open_browser
    
    # setup ui engine
    open_browser = options.open_browser if options is not None else False
    methods = []
    if sys.platform == "darwin":
        methods.append(_cocoa)
    elif 'DISPLAY' in os.environ or sys.platform == "win32":
        methods.append(_tk)

    if options is not None and options.headless:
        methods = []
    try:
        for method in methods:
            try:
                ui, name = method()
                if not hasattr(ui, 'has_ui'):
                    ui.has_ui = True
                if hasattr(ui, 'init'):
                    ui.init()
                break
            except (KeyboardInterrupt, SystemExit, gevent.GreenletExit):
                raise
            except:
                ui, name = None, None
                log.unhandled_exception('error using ui method')
        else:
            ui, name = _headless()
    finally:
        log.debug('using {}'.format(name))

    # start splash screen
    if hasattr(ui, 'Splash') and not options.disable_splash:
        splash = ui.Splash()

        event.add('loader:initialized', terminate_splash)

def terminate():
    terminate_splash()
    if hasattr(ui, 'terminate'):
        ui.terminate()

def main_loop():
    try:
        ui.main_loop()
    except AttributeError:
        gevent.run()

def set_splash_text(text):
    if splash:
        splash.set_text(text)

def show_splash():
    if splash:
        splash.show()

def hide_splash():
    if splash:
        splash.hide()

def terminate_splash(e=None):
    global splash
    if splash:
        splash.close()
        splash = None

def browser_has_focus():
    try:
        return ui.browser_has_focus()
    except:
        return None

def browser_to_focus(open_new_tab=True, has_focus=None):
    try:
        if ui.browser_to_focus(has_focus):
            return True
    except:
        pass
    if open_new_tab:
        from . import login
        webbrowser.open_new_tab(login.get_sso_url())
        return True
    return False

@event.register('loader:initialized')
def loader_initialized(e):
    if open_browser is True and config.open_browser is True:
        from . import api, loader
        if api in loader.post_objects:
            api.wait_connected()
            browser_to_focus(True)
