@echo off
title Lanzador de Flota Distribuida
echo ========================================================
echo   INICIANDO SIMULADOR NAVAL DISTRIBUIDO (NS + CENTRALES + FLOTA)
echo ========================================================

echo 1. Levantando Name Server independiente...
start "NAME SERVER" cmd /k "title NAME SERVER && python3 nameserver.py --host 127.0.0.1 --port 9090"

ping -n 3 127.0.0.1 >nul

echo 2. Levantando Central 1 (Principal)...
start "CENTRAL 1 - Principal" cmd /k "title CENTRAL 1 && python3 central.py 1 --host 127.0.0.1 --port 8080 --ns-host 127.0.0.1 --ns-port 9090 --http-port 5000 --peers-http 2:127.0.0.1:5001"

ping -n 2 127.0.0.1 >nul

echo 3. Levantando Central 2 (Respaldo)...
start "CENTRAL 2 - Respaldo" cmd /k "title CENTRAL 2 && python3 central.py 2 --host 127.0.0.1 --port 8081 --ns-host 127.0.0.1 --ns-port 9090 --http-port 5001 --peers-http 1:127.0.0.1:5000"

ping -n 2 127.0.0.1 >nul

echo 4. Levantando Barco 1 (Destructor Alfa)...
start "BARCO 1 - Destructor Alfa" cmd /k "title BARCO 1 - Destructor Alfa && python3 barco.py 1 --nombre "Destructor Alfa" --host 127.0.0.1 --port 9001 --central 127.0.0.1:9090 --lat -34.6 --lon -58.4 --rumbo 45 --vel 14.5"

ping -n 2 127.0.0.1 >nul

echo 5. Levantando Barco 2 (Fragata Beta)...
start "BARCO 2 - Fragata Beta" cmd /k "title BARCO 2 - Fragata Beta && python3 barco.py 2 --nombre "Fragata Beta" --host 127.0.0.1 --port 9002 --central 127.0.0.1:9090 --lat -20.0 --lon -35.0 --rumbo 135 --vel 18.0"

ping -n 2 127.0.0.1 >nul

echo 6. Levantando Barco 3 (Corbeta Gamma)...
start "BARCO 3 - Corbeta Gamma" cmd /k "title BARCO 3 - Corbeta Gamma && python3 barco.py 3 --nombre "Corbeta Gamma" --host 127.0.0.1 --port 9003 --central 127.0.0.1:9090 --lat 10.0 --lon -20.0 --rumbo 210 --vel 12.0"

echo.
echo ========================================================
echo Todas las consolas han sido iniciadas.
echo Dashboard Central 1 (Principal): http://localhost:5000
echo Dashboard Central 2 (Respaldo):  http://localhost:5001
echo.
echo Recuerde: Una vez que los 3 barcos figuren como registrados,
echo escriba "formar flota" en la consola de la CENTRAL 1 (Principal).
echo Si matan la Central 1, la Central 2 la reemplaza sola y el
echo dashboard los redirige automaticamente al nuevo activo.
echo ========================================================
