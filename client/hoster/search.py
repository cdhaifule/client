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

import base64
import gevent

from gevent import Timeout
from gevent.pool import Group
from gevent.lock import Semaphore
from gevent.event import Event
from PIL import Image, ImageOps
from cStringIO import StringIO as BytesIO

from . import manager, this, util
from .. import interface
from ..cache import CachedDict, lru_file_cache
from ..scheme import Table, Column, DelayedListener, register, transaction, get_by_uuid

thumb_cache = lru_file_cache
search_cache = CachedDict(3600, lambda search: search.delete())

WAIT_BEFORE_DELETE = 10


####################### scheme listener

class SearchListener(DelayedListener):
    def __init__(self):
        DelayedListener.__init__(self, 'search', 0)

    def on_commit(self, update):
        responders = dict()
        for data in update.values():
            if data['action'] == 'delete':
                #print "!"*50, 'deleted', data['table']
                continue
            if data['table'] == 'search':
                search = get_by_uuid(data['id'])
            elif data['table'] == 'search_result':
                result = get_by_uuid(data['id'])
                if not result.public.is_set():
                    continue
                search = result.search
            else:
                raise RuntimeError('unknown search table: {}'.format(data['table']))
            k = (search.responder, search.pushed_event)
            if k not in responders:
                responders[k] = list()
            responders[k].append(data)

        for (responder, event), data in responders.iteritems():
            if responder is not None: # responder can be none when interface function is called directly
                responder.send(data)
            event.set()
            #print data

register(SearchListener())


####################### templates

templates = dict()

class Template(object):
    def __init__(self, name, weight, content, thumb_dimensions=None):
        self.name = name
        self.weight = weight
        self.content = content
        self.thumb_dimensions = thumb_dimensions
        templates[name] = self

default_template = Template(
    'list',
    0,
    '<div>html fuckup...</div>')

Template(
    'thumbs',
    1,
    '<div>html fuckup...</div>',
    (120, 70))


####################### search objects

class Search(Table):
    _table_name = 'search'
    _table_collection = search_cache

    id = Column('search')
    plugins = Column('search')
    query = Column('search')
    tags = Column('search')

    contexts = Column(change_affects=['more'])
    more = Column('search', always_use_getter=True, getter_cached=True)
    template = Column('search')

    def __init__(self, id=None, plugins=None, query=None, tags=None, template=None):
        self.id = id
        self.plugins = plugins
        self.query = query
        self.tags = tags

        self._i = 0
        self.last_position = 0
        self.responder = None
        self.group = Group()
        self.lock = Semaphore()
        self.pushed_event = Event()
        self.public_results = list()
        self.garbage_greenlet = None

        # add the search contexts
        self.template = None
        self.contexts = list()
        for plugin in self.plugins:
            self.contexts.append(Context(self, plugin))

        # set the template type
        if template is not None:
            self.template = templates[template]
        else:
            for ctx in self.contexts:
                try:
                    template = templates[ctx.plugin.search['template']]
                except KeyError:
                    #log.warning('template "{}" of plugin "{}" not found'.format(ctx.plugin.search['template'], ctx.name))
                    continue
                if self.template is None or template.weight < self.template.weight:
                    self.template = template
            if self.template is None:
                self.template = default_template

    def run(self, responder, min_results, max_results):
        with self.lock:
            return self.spawn(self._run, responder, min_results, max_results).get()

    def _run(self, responder, min_results, max_results):
        # wait until search query is complete and set current responder
        if self.responder and self.responder != responder:
            self.pushed_event.clear()
            self.group.join()
            with Timeout(3):
                self.pushed_event.wait()
        self.responder = responder
        self.min_results = min_results
        self.max_results = max_results

        # spawn search tasks
        start_position = self.last_position
        with transaction:
            while True:
                group = Group()
                for ctx in self.contexts:
                    if not ctx.target_results_reached():
                        g = self.spawn(ctx.run)
                        group.add(g)
                group.join()

                # remove empty search contexts
                for ctx in self.contexts[:]:
                    if not ctx.results:
                        self.remove_ctx(ctx)

                if not self.contexts:
                    break

                # create result list
                while max_results is None or self.last_position - start_position < max_results:
                    ctx = self.next_result_ctx()
                    if not ctx.results:
                        break
                    result = ctx.results.pop(0)
                    self.publish_result(result)

                # remove useless contexts
                for ctx in self.contexts[:]:
                    if not ctx.results and ctx.next is None:
                        self.remove_ctx(ctx)

                if not self.contexts or min_results is None or self.last_position - start_position >= min_results:
                    break

        # add this search to cache to allow search_more
        if self.more:
            search_cache[self.id] = self
        elif self.id in search_cache:
            del search_cache[self.id]
            self.garbage_func()

        return self.last_position - start_position

    def publish_result(self, result):
        self.public_results.append(result)
        self.last_position += 1
        result.position = self.last_position
        result.public.set()
        result.set_table_dirty('search')
        result.garbage_func()

    def next_result_ctx(self):
        try:
            ctx = self.contexts[self._i]
            self._i += 1
            return ctx
        except IndexError:
            self._i = 0
            return self.next_result_ctx()

    def remove_ctx(self, ctx):
        i = self.contexts.index(ctx)
        if i <= self._i:
            self._i -= 1
        with transaction:
            self.contexts.remove(ctx)

    def on_get_more(self, value):
        if self.contexts:
            return True
        return False

    def on_get_template(self, value):
        return value.name if value else None

    def spawn(self, func, *args, **kwargs):
        return self.group.spawn(func, *args, **kwargs)

    # garbage

    def garbage_func(self):
        if self.garbage_greenlet is None:
            self.garbage_greenlet = gevent.spawn(self._garbage_func)
        else:
            raise RuntimeError('garbage_func called twice')

    def _garbage_func(self):
        gevent.sleep(WAIT_BEFORE_DELETE)
        gevent.spawn(self.delete)

    # delete

    def delete(self):
        if not self._table_deleted:
            search_cache.pop(self, None)
            if self.garbage_greenlet:
                self.garbage_greenlet.kill()
            self.group.kill()
            with transaction:
                for ctx in self.contexts[:]:
                    ctx.delete()
                for result in self.public_results[:]:
                    result.delete()
                self.table_delete()

class Context(object):
    def __init__(self, search, name):
        self.search = search
        self.name = name

        self.account = None
        self.position = None
        self.next = 0
        
        self.results = list()
        self.referer = None

        self.sub_lock = Semaphore(3)
        self.thumb_lock = Semaphore(3)

    def run(self):
        self.account = self.plugin.get_account('search', None)
        self.position = self.next
        self.next = None
        if (not self.search.query or self.search.query == '_EMPTY') and self.plugin.search.get('empty'):
            self.plugin.on_search_empty(self)
        else:
            self.plugin.on_search(self, self.search.query)

    def add_result(self, *args, **kwargs):
        with transaction:
            r = Result(self, *args, **kwargs)
        self.results.append(r)
        return r
    add = add_result
    __call__ = add_result

    def spawn(self, func, *args, **kwargs):
        return self.search.spawn(func, *args, **kwargs)

    @property
    def plugin(self):
        if not hasattr(self, '_plugin'):
            try:
                plugin = manager.find_by_name(self.name)
            except KeyError:
                pass
            if plugin is None:
                raise ValueError('plugin {} not found'.format(self.name))
            self._plugin = plugin
        return self._plugin

    def target_results_reached(self, index=None):
        if self.search.max_results is None:
            return True
        if (index or len(self.results)) > self.search.max_results/len(self.search.contexts):
            return True
        return False

    def delete(self):
        self.search.contexts.remove(self)
        for r in self.results[:]:
            r.delete()
        
class Result(Table):
    _table_name = 'search_result'
    _table_auto_transaction = True

    context = Column()

    id = Column('search')
    name = Column('search')
    search = Column('search')

    title = Column('search')
    thumb = Column('search')
    duration = Column('search')
    url = Column('search')
    description = Column('search')

    exists = Column('search')
    position = Column('search')

    extra = Column() # url extra

    def __init__(self, context, title=None, thumb=None, duration=None, url=None, description=None, extra=None):
        self.context = context
        self.name = self.context.name
        self.search = context.search

        self.title = title
        self.thumb = thumb
        self.duration = duration
        self.url = url
        self.description = description
        self.extra = extra

        self.group = Group()
        self.public = Event()
        self.garbage_greenlet = None

    # spawn functions

    def _spawn(self, func, *args, **kwargs):
        g = self.context.spawn(func, *args, **kwargs)
        self.group.add(g)
        return g

    def spawn_sub(self, func, *args, **kwargs):
        """spawn a subtask for this search
        """
        return self._spawn(self._sub_task, func, *args, **kwargs)
    spawn = spawn_sub

    def _sub_task(self, func, *args, **kwargs):
        # don't wait when we have < approx needed result
        if self.context.target_results_reached(self.context.results.index(self)):
            self.public.wait()
        with self.context.sub_lock:
            return func(self, *args, **kwargs)

    # garbage

    def garbage_func(self):
        if self.garbage_greenlet is None:
            self.garbage_greenlet = gevent.spawn(self._garbage_func)
        else:
            raise RuntimeError('garbage_func called twice')

    def _garbage_func(self):
        self.public.wait()
        self.group.join()
        gevent.sleep(WAIT_BEFORE_DELETE)
        gevent.spawn(self.delete)

    # setter/getter

    def on_get_search(self, value):
        return value.id

    # url

    def _create_url(self, url, extra):
        if url and extra is not None:
            url = util.add_extra(url, extra)
        try:
            if url is None:
                self.exists = False
            else:
                from ..core import url_exists
                self.exists = url_exists(url) and True or False
        except TypeError:
            self.exists = False
            url = None
        return url

    def on_set_url(self, value):
        return self._create_url(value, self.extra)

    def on_set_extra(self, value):
        return self._create_url(self.url, value)

    # thumb functions

    def on_set_thumb(self, value):
        if self.search.template.thumb_dimensions is None:
            return None
        if not value or '://' not in value:
            return value
        try:
            data = thumb_cache[value]
        except KeyError:
            pass
        else:
            return base64.b64encode(data)
        self.load_thumb(value)
        return None

    def load_thumb(self, url, resp=None, **kwargs):
        """downloads a image and converts it to a thumbnail
        """
        self._spawn(self._load_thumb, url, resp, **kwargs)

    def _load_thumb(self, url, resp, **kwargs):
        # don't wait when we have < approx needed result
        if self.context.target_results_reached(self.context.results.index(self)):
            self.public.wait()
        with self.context.thumb_lock:
            # download data
            if resp is None:
                resp = self.context.account
            resp = resp.get(url, **kwargs)
            data = resp.content

            # create thumbnail
            data = self._create_thumb(data)

            # save thumb to cache and table
            thumb_cache[url] = data
            self.thumb = base64.b64encode(data)

    def _create_thumb(self, data):
        img = Image.open(BytesIO(data))
        dim_x, dim_y = self.search.template.thumb_dimensions
        x, y = img.size
        img = img.convert("RGB")
        img = ImageOps.fit(img, (dim_x, dim_y), Image.ANTIALIAS)
        data = BytesIO()
        img.save(data, "JPEG", quality=40, optimize=True, progressive=True)
        return data.getvalue()

    # delete

    def delete(self):
        if not self._table_deleted:
            if self.garbage_greenlet:
                self.garbage_greenlet.kill()
            self.group.kill()
            if self in self.context.results:
                self.context.results.remove(self)
            if self in self.search.public_results:
                self.search.public_results.remove(self)
            with transaction:
                self.table_delete()

class Input(object):
    def __init__(self, query, template='list', plugin=None, id=None):
        if plugin in None:
            if hasattr(this.localctx, "hoster"):
                plugin = this.localctx.hoster.name
            else:
                raise RuntimeError('need a plugin name for search input')

        with transaction:
            self.search = Search(id=None, query=query, plugins=[plugin], template=template)

        self.context = self.search.contexts[0]
        self.context.position = self.context.next
        self.context.next = None

    def __enter__(self):
        pass

    def __exit__(self, exc_type, exc_value, traceback):
        self.search.garbage_func()

    def add_result(self, *args, **kwargs):
        result = self.context.add_result(*args, **kwargs)
        self.search.publish_result(result)
        return result
    add = add_result
    __call__ = add_result


####################### interface

@interface.register
class HosterInterface(interface.Interface):
    name = "hoster.search"

    def search(id=None, plugins=None, query=None, tags=["other"], min_results=None, max_results=50, responder=None):
        with transaction:
            search = Search(id=id, plugins=plugins, query=query, tags=tags)
        return search.run(responder, min_results, max_results)

    def search_more(id=None, min_results=None, max_results=50, responder=None):
        if id not in search_cache:
            return 0
        return search_cache[id].run(responder, min_results, max_results)

    def close(id):
        if id not in search_cache:
            return False
        search_cache[id].delete()
        return True
