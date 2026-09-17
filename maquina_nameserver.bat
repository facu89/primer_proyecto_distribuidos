@echo off
REM ============================================================
REM  CONFIGURACION: correr en la maquina que va a hostear el Name Server.
REM  Cambiar NS_HOST por la IP de ESTA maquina en la red (ver "ipconfig").
set NS_HOST=192.168.88.206
set NS_PORT=9090
REM ============================================================

title NAME SERVER
python3 nameserver.py --host %NS_HOST% --port %NS_PORT%
