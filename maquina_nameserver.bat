@echo off
REM ============================================================
REM  CONFIGURACION: correr en la maquina que va a hostear el Name Server.
REM  Cambiar NS_HOST por la IP de ESTA maquina en la red (ver "ipconfig").
set NS_HOST=192.168.1.53
set NS_PORT=9090
REM ============================================================

title NAME SERVER
python nameserver.py --host %NS_HOST% --port %NS_PORT%
