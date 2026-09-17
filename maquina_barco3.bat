@echo off
REM ============================================================
REM  CONFIGURACION: correr en la maquina del Barco 3.
REM  MI_HOST = IP de ESTA maquina (ver "ipconfig"). NO dejar que se autodetecte:
REM            si esta red no tiene salida a internet, la autodeteccion falla y
REM            el barco termina escuchando en 127.0.0.1, invisible para las demas maquinas.
REM  NS_HOST = IP de la maquina donde corre nameserver.py (maquina_nameserver.bat).
set MI_HOST=192.168.88.206
set NS_HOST=192.168.88.206
set NS_PORT=9090
REM ============================================================

title BARCO 3 - Corbeta Gamma
python3 barco.py 3 --nombre "Corbeta Gamma" --host %MI_HOST% --port 9003 --central %NS_HOST%:%NS_PORT% --lat 10.0 --lon -20.0 --rumbo 210 --vel 12.0
