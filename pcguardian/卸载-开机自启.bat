@echo off
chcp 65001 >nul
title PCGuardian 卸载开机自启（家长专用）
echo 正在移除 PC Guardian ...
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v PCGuardian /f >nul 2>&1
schtasks /delete /tn "PCGuardianWatch" /f >nul 2>&1
taskkill /f /im sysguard.exe >nul 2>&1
taskkill /f /im PCGuardian.exe >nul 2>&1
echo [OK] 已移除：开机自启、看门狗计划任务，并结束管控进程。
echo 电脑重启后不再自动锁定。如需重新启用，运行：安装-开机自启.bat
pause
