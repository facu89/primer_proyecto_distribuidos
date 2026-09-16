@echo off
REM ============================================================
REM  CONFIGURACION: correr en la maquina del Barco 1.
REM  MI_HOST = IP de ESTA maquina (ver "ipconfig"). NO dejar que se autodetecte:
REM            si esta red no tiene salida a internet, la autodeteccion falla y
REM            el barco termina escuchando en 127.0.0.1, invisible para las demas maquinas.
REM  NS_HOST = IP de la maquina donde corre nameserver.py (maquina_nameserver.bat).
set MI_HOST=192.168.1.53
set NS_HOST=192.168.1.53
set NS_PORT=9090
REM ============================================================

title BARCO 1 - Destructor Alfa
python barco.py 1 --nombre "Destructor Alfa" --host %MI_HOST% --port 9001 --central %NS_HOST%:%NS_PORT% --lat -34.6 --lon -58.4 --rumbo 45 --vel 14.5
