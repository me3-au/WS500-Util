@echo off
REM Quick-launch the WS500 Util GUI with the sample file loaded.
REM Run from the project folder.

setlocal
cd /d "%~dp0"

python ws500_util.py
endlocal
