@echo off
where py >nul 2>nul && (py ver_monitores.py) || (python ver_monitores.py)
pause
