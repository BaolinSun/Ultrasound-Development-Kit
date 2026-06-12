@echo off
chcp 65001 >nul
echo ============================================================================================
echo           The cubdl environment is being started and the program is running.
echo ============================================================================================
echo.

:: 激活 conda 环境 并 执行python脚本
call conda activate cubdl
python ultrasound_desktop_app.py

echo.
echo ==============================================
echo              Execution completed
echo ==============================================
pause
