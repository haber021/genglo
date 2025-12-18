@echo off
REM ============================================
REM Django Server for Mobile App Access
REM ============================================
REM This script starts Django server bound to 0.0.0.0:8000
REM which allows mobile devices on the same network to connect.
REM
REM IMPORTANT: Make sure your mobile device and computer
REM are on the same Wi-Fi network!
REM ============================================

echo.
echo ============================================
echo Starting Django Server for Mobile App...
echo ============================================
echo.
echo Server will be accessible at:
echo   - http://localhost:8000 (on this computer)
echo   - http://YOUR_IP:8000 (on mobile devices)
echo.
echo To find your IP address:
echo   Windows: ipconfig ^| findstr IPv4
echo   Mac/Linux: ifconfig or ip addr
echo.
echo Press Ctrl+C to stop the server
echo.
echo ============================================
echo.

python manage.py runserver 0.0.0.0:8000

