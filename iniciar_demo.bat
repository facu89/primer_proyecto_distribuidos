@echo off
title Lanzador de Flota Distribuida
echo ========================================================
echo   INICIANDO SIMULADOR NAVAL DISTRIBUIDO (CENTRAL + FLOTA)
echo ========================================================

echo 1. Levantando Central en nueva ventana...
start "CENTRAL - Estacion Naval" cmd /k "title CENTRAL && python central.py --host 127.0.0.1 --port 8080 --ns-port 9090 --http-port 5000"

ping -n 3 127.0.0.1 >nul

echo 2. Levantando Barco 1 (Destructor Alfa)...
start "BARCO 1 - Destructor Alfa" cmd /k "title BARCO 1 - Destructor Alfa && python barco.py 1 --nombre "Destructor Alfa" --host 127.0.0.1 --port 9001 --central 127.0.0.1:9090 --lat -34.6 --lon -58.4 --rumbo 45 --vel 14.5"

ping -n 2 127.0.0.1 >nul

echo 3. Levantando Barco 2 (Fragata Beta)...
start "BARCO 2 - Fragata Beta" cmd /k "title BARCO 2 - Fragata Beta && python barco.py 2 --nombre "Fragata Beta" --host 127.0.0.1 --port 9002 --central 127.0.0.1:9090 --lat -20.0 --lon -35.0 --rumbo 135 --vel 18.0"

ping -n 2 127.0.0.1 >nul

echo 4. Levantando Barco 3 (Corbeta Gamma)...
start "BARCO 3 - Corbeta Gamma" cmd /k "title BARCO 3 - Corbeta Gamma && python barco.py 3 --nombre "Corbeta Gamma" --host 127.0.0.1 --port 9003 --central 127.0.0.1:9090 --lat 10.0 --lon -20.0 --rumbo 210 --vel 12.0"

echo.
echo ========================================================
echo Todas las consolas han sido iniciadas.
echo Dashboard disponible en: http://localhost:5000
echo.
echo Recuerde: Una vez que los 3 barcos figuren como registrados,
echo escriba "formar flota" en la consola de la CENTRAL.
echo ========================================================
