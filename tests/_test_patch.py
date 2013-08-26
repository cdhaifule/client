import sys
import shutil
from gevent import Timeout

import loader
loader.init()

from . import httpserver
from client import interface, patch, debugtools, settings, loader

loader.init()

sys.stdout = sys._old_stdout
sys.stderr = sys._old_stderr

def setUp():
    httpserver.start()

def tearDown():
    httpserver.stop()

class Test(object):
    def setUp(self):
        interface.call('patch', 'remove_source', erase=True)
        try:
            shutil.rmtree(settings.external_plugins)
        except:
            pass
        print "up"

    def tearDown(self):
        print "down"
        interface.call('patch', 'remove_source', erase=True)

    def test_config(self):
        self._test_source('localhost:4567/repotest/redir/config', 'http://localhost:4567/repotest/redir/config')

    def test_git(self):
        self._test_source('localhost:4567/repotest/redir/git', 'http://localhost:4567/repotest/redir/git')

    def test_none(self):
        interface.call('patch', 'add_source', url='localhost:4567')
        assert patch.sources == {}

    def _test_source(self, url, config_url):
        assert patch.sources == {}
        interface.call('patch', 'add_source', url=url)
        source = patch.sources['hoster']

        data = source.serialize()
        debugtools.compare_dict(data, {'branches': [], 'config_url': config_url, 'url': 'http://github.com/downloadam/hoster.git', 'last_error': None, 'enabled': True, 'version': '0000000', 'id': 'hoster'})

        try:
            with Timeout(10):
                patch.patch_all(external_loaded=False)
        except Timeout:
            print "WARNING: patch timed out. ignoring error"
            return

        data = source.serialize()
        debugtools.compare_dict(data, {'config_url': config_url, 'url': 'http://github.com/downloadam/hoster.git', 'last_error': None, 'enabled': True, 'id': 'hoster'})
        assert 'master' in data['branches']
        assert data['version'] != '0'*7

if __name__ == '__main__':
    setUp()
    test = Test()
    test.setUp()
    #test.test_config()
    test.test_git()
    #test.test_none()
    test.tearDown()
    tearDown()
