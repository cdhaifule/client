@echo off
:: script to get admin rights on windows and execute registry script %2 after %1 - 1 seconds
ping -n %1 127.0.0.1 >NUL
regedit.exe -s %2
del "%2"
