@echo off
REM ============================================================
REM  CONFIGURACION: correr en la maquina del Barco 2.
REM  MI_HOST = IP de ESTA maquina (ver "ipconfig"). NO dejar que se autodetecte:
REM            si esta red no tiene salida a internet, la autodeteccion falla y
REM            el barco termina escuchando en 127.0.0.1, invisible para las demas maquinas.
REM  NS_HOST = IP de la maquina donde corre nameserver.py (maquina_nameserver.bat).
set MI_HOST=192.168.1.53    
set NS_HOST=192.168.1.53
set NS_PORT=9090
REM ============================================================

title BARCO 2 - Fragata Beta
python barco.py 2 --nombre "Fragata Beta" --host %MI_HOST% --port 9002 --central %NS_HOST%:%NS_PORT% --lat -20.0 --lon -35.0 --rumbo 135 --vel 18.0
