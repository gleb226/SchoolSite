@echo off
chcp 65001 > nul
echo ===================================================
echo   SchoolSite - Uzhhorod Lyceum No.3
echo ===================================================

py -3.12 start.py || python start.py

pause
