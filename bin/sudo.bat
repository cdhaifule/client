@echo off
:start
if exist "%1" goto run
if exist "%2" goto end
ping -n 2 127.0.0.1 >NUL
goto start

:run
regedit.exe -s %1
del %1
goto start

:end
del "%2"
