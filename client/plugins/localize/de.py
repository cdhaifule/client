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

ID = 'de'
NAME = 'Deutsch'
FALLBACK = ['en', 'sp', 'fr']
TEXT = dict()

# RPC

rpc__options = "RPC Optionen"
rpc__usage = ' [RPC Befehl 1] [RPC Befehl 2] [...]'
rpc__epilog = u"""
Du kannst die Interfacefunktionen direkt von der Kommandozeile aus aufrufen:
    download.am "module.command arg1=param1 -- arg2=param2" "mod.cmd arg1=param1 -- arg2=param2 -- ..."

Jedes Argument ist ein Interface aufruf. Die Interface Parameter werden Mit -- seperiert.
Um eine Übersicht über verfügbare module zu bekommen:
    download.am "interface.list_modules"

Du kannst json Objekte als Parameter angeben.
Alle Rückgabewerte werden json encodiert.

Wenn das Programm nicht schon läuft wird es gestartet und daraufhin die Befehle aufgeführt.
Du kannst die Option --exit-after-exec benutzen um das Programm nach dem ausführen der Befehle
zu beenden.

"""
rpc__exit_after_exec = u'beende das Programm nachdem alle RPC Befehle ausgeführt worden sind. Dies ist das Standart verhalten wenn das Programm bereits läuft.'

# logger

logger__options = 'Log Optionen'
logger__valid_levels = u"gültige Log-Levels: DEBUG, INFO, WARNING, ERROR, CRITICAL"
logger__log_level = u'setzt das Log-Level für die Konsolenausgabe (Default: DEBUG)'
logger__log_file = u'setzt die Log Datei. Wenn FILE "off" ist wird nicht in deine Datei geloggt.'
logger__log_file_level = u'setzt das Log-Level für die Log Datei (Default: DEBUG)'

# login

login__options = 'Login Optionen'
login__username = 'Login Benutzername'
login__password = 'Login Passwort'
login__save_password = 'speicher das Passwort in der Einstellungsdatei. Du musst das Passwort dann nicht erneut eingeben'

# UI

ui__options = u'Benutzer Interface (GUI) Optionen'
ui__headless = u'kein Interface benutzen. Setze die Login Informationen per Kommandozeile'
ui__open_browser = u'öffne den Browser beim Start (wenn die Einstellungsvariable auf "true" ist)'
ui__disable_splash = u'kein Splash-Screen beim Start anzeigen'

# loader

loader__usage = '%prog [Optionen]'
loader__help = 'zeigt diese Meldung und beendet das Programm'

# api

api__options = 'API Einstellungen'
api__api_log = 'Logge den API Datenverkehr'

#############################

# systray

TEXT["Open"] = 'Öffnen'
TEXT["Logout"] = 'Abmelden'
TEXT["Select browser"] = 'Browser ändern'
TEXT["Quit"] = 'Beenden'

# general

TEXT['OK'] = 'OK'
TEXT['Cancel'] = 'Abbrechen'
TEXT['Yes'] = 'Ja'
TEXT['No'] = 'Nein'
TEXT['Remember decision?'] = 'Entscheidung merken?'
TEXT['No, and don\'t ask again'] = 'Nein, und nicht nochmal nachfragen'

# closed beta

TEXT['The project is currently in closed beta state.\nYou need an invite code to register an account\non the website, then you can login.'] = \
    'Das Projekt ist momentan im geschlossenen Beta-Stadium.\nDu benötigst einen Einladungscode um einen\nAccount auf der Webseite anzulegen. Danach kannst du dich erst anmelden'

# login

TEXT['Please input your login informations'] = 'Bitte gebe hier deine Registrierungsdaten ein'
TEXT['E-Mail:'] = 'E-Mail:'
TEXT['Password:'] = 'Passwort:'
TEXT['Save password'] = 'Passwort speichern'
TEXT['Forgot password?'] = 'Passwort vergessen?'
TEXT['Register'] = 'Registrieren'

# patch

TEXT['Some new updates were installed. You have to restart Download.am to apply them.'] = \
    'Es wurden neue Updates installiert. Um sie anzuwenden musst du Download.am neu starten.'
TEXT['Restart now?'] = 'Jetzt neu starten?'
TEXT['Ask me later'] = 'Später nochmal fragen'
TEXT['When downloads are complete'] = 'Wenn die Downloads fertig sind'

# plugins.hoster.http

TEXT['Found #{num} links on #{domain}. Do you want to add them?'] = 'Es wurden #{num} Links auf #{domain} gefunden. Möchtest du sie hinzufügen?'

# input

TEXT['Please input the following Captcha:'] = 'Bitte gebe das folgende Captcha ein:'
TEXT['Please click on the right place:'] = 'Bitte klicke auf die richtige Stelle:'
TEXT['Enter password:'] = 'Passwort eingeben:'
TEXT['I understand'] = 'Ich verstehe'
TEXT['Connect as guest'] = 'Als Gast verbinden'

# webbrowser

TEXT['Your current default browser #{browser} is not compatible with Download.am.'] = 'Dein aktueller Standartbrowser #{browser} ist nicht mit Download.am kompatibel.'
TEXT['Please select a browser you like to use with Download.am.'] = 'Bitte wähle einen Browser den Du mit Download.am nutzen möchtest.'
TEXT['You have no compatible webbrowser installed.'] = 'Du hast keinen kompatiblen Browser installiert.'
TEXT['The best choice is Chrome, Firefox or Opera Next. You find the download links below.'] = 'Die beste Wahl ist Chrome, Firefox oder Opera Next. Nachfolgend findest du die Downloadlinks.'

# login popup
TEXT["You reached the download.am client on machine"] = "Hier ist download.am Client auf"
TEXT["Would you like to login now?"] = "Möchtest du dich mit ihm verbinden?"
