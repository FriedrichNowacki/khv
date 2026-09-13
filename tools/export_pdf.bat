@echo off
title Markdown zu PDF Export
cd /d "%~dp0\.."
echo Konvertiere Dokumentation zu PDF...
echo.
python tools\md_to_pdf.py README.md
python tools\md_to_pdf.py docs\wilo_varios_pico_stg.md
echo.
echo Fertig! Die PDFs wurden erstellt.
pause
