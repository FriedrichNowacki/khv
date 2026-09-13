@echo off
title 3D-Visualisierung Pumpenkennlinien Wilo Varios PICO-STG
cd /d "%~dp0\.."
echo Starte 3D-Visualisierung und Interpolationspruefung...
echo.
python tools\plot_pump_curves_3d.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Fehler beim Ausfuehren des Skripts!
    pause
)
