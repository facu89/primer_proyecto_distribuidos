@echo off
REM ============================================================
REM  CONFIGURACION: correr en la maquina de la Central 1.
REM  MI_HOST    = IP de ESTA maquina (ver "ipconfig"). NO dejar que se autodetecte:
REM               si esta red no tiene salida a internet, la autodeteccion falla y
REM               todo termina escuchando en 127.0.0.1, invisible para las demas maquinas.
REM  NS_HOST    = IP de la maquina donde corre nameserver.py (maquina_nameserver.bat).
REM  CENTRAL2_HTTP = IP:puerto del dashboard de la Central 2, para que el
REM  navegador pueda redirigir solo ahi si esta Central se cae.
set MI_HOST=192.168.88.206
set NS_HOST=192.168.88.206
set NS_PORT=9090
set CENTRAL2_HTTP=192.168.88.206:5000
REM ============================================================

title CENTRAL 1
python3 central.py 1 --host %MI_HOST% --port 8080 --ns-host %NS_HOST% --ns-port %NS_PORT% --http-port 5000 --peers-http 2:%CENTRAL2_HTTP%

