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

from __future__ import absolute_import

import locale

from . import settings, logger, plugintools
from .config import globalconfig

config = globalconfig.new('localize')
config.default('lang', None, str)

log = logger.get('localize')

default_lang = 'en'

available_languages = dict()
current_language = None

class Translate(object):
    def translate(self, key):
        for mod in current_language:
            if hasattr(mod, key):
                val = getattr(mod, key)
                if not isinstance(val, unicode):
                    return val.decode("utf-8")
                return val
        return key
    __getattr__ = translate
    __getitem__ = translate

    def text(self, key):
        for mod in current_language:
            if key in mod.TEXT:
                val = mod.TEXT[key]
                if not isinstance(val, unicode):
                    return val.decode("utf-8")
                return val
        return key

T = _T = Translate()
X = _X = T.text
translate = T.translate

def load_language(lang):
    global current_language

    current_language = list()
    for lang in [lang]+available_languages[lang].FALLBACK:
        try:
            current_language.append(available_languages[lang])
        except KeyError:
            pass

    log.info('loaded language {} with fallback {}'.format(current_language[0].ID, ', '.join(current_language[0].FALLBACK)))

def init_pre():
    global available_languages

    for mod in plugintools.itermodules('localize'):
        if mod.ID in available_languages:
            for k, v in mod.__dict__.iteritems():
                if not k.startswith('_') and '__' in k and not hasattr(available_languages[mod.ID], k):
                    setattr(available_languages[mod.ID], k, v)
            for k, v in mod.TEXT.iteritems():
                if k not in available_languages[mod.ID].TEXT:
                    available_languages[mod.ID].TEXT[k] = v
        else:
            available_languages[mod.ID] = mod

    try:
        with open(settings.localize_file, 'rb') as f:
            lang = f.read().strip()
    except:
        lang = None

    if lang is None or lang not in available_languages:
        syslang = locale.getdefaultlocale()
        if syslang is not None and syslang[0] is not None:
            lang = syslang[0][0:2]
            if lang not in available_languages:
                lang = default_lang
        else:
            lang = default_lang

        with open(settings.localize_file, 'wb') as f:
            f.write(lang)

    load_language(lang)

def init():
    try:
        with open(settings.localize_file, 'rb') as f:
            config.lang = f.read().strip()
    except:
        config.lang = default_lang
        load_language(config.lang)
    log.info('set language to {} ({})'.format(config.lang, available_languages[config.lang].NAME))
