@echo off
echo Instalando librerias de Python (mss, pillow, pystray)...
where py >nul 2>nul && (py -m pip install --upgrade mss pillow pystray) || (python -m pip install --upgrade mss pillow pystray)
echo.
echo Listo. Ya puedes abrir tablet_monitor.pyw con doble clic.
pause
