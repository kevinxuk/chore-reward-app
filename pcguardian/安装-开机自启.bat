@echo off
chcp 65001 >nul
title PCGuardian 安装开机自启
echo ============================================
echo   PC Guardian - 儿童电脑使用时间管控
echo   安装开机自启 + 防关闭看门狗（当前账户）
echo ============================================
echo.

set "DIR=%~dp0"

if exist "%DIR%PCGuardian.exe" goto :exe_mode
goto :py_mode

:exe_mode
rem ---- exe 版（无需 Python，推荐）----
copy /y "%DIR%PCGuardian.exe" "%DIR%sysguard.exe" >nul
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v PCGuardian /t REG_SZ /d "\"%DIR%sysguard.exe\"" /f >nul
echo   运行方式 : PCGuardian.exe（已复制为 sysguard.exe 伪装进程名）
goto :watchdog

:py_mode
rem ---- 脚本版回退（需要本机装有 Python）----
set "PYW="
for /f "delims=" %%i in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%i"
if not defined PYW for /f "delims=" %%i in ('where python 2^>nul') do (
    if exist "%%~dpipythonw.exe" if not defined PYW set "PYW=%%~dpipythonw.exe"
)
if not defined PYW set "PYW=C:\Users\Administrator\AppData\Roaming\tokeny\runtimes\python\pythonw.exe"
if not exist "%PYW%" (
    echo [X] 未找到 PCGuardian.exe，也未找到 pythonw.exe。
    echo     请把 PCGuardian.exe 放到本文件夹后重新运行本脚本。
    pause & exit /b 1
)
copy /y "%PYW%" "%DIR%sysguard.exe" >nul
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v PCGuardian /t REG_SZ /d "\"%PYW%\" \"%DIR%guardian.pyw\"" /f >nul
echo   运行方式 : pythonw.exe + guardian.pyw
goto :watchdog

:watchdog
rem ---- 看门狗计划任务：每 1 分钟检查，进程被结束则自动重新拉起 ----
schtasks /create /tn "PCGuardianWatch" /tr "wscript.exe \"%DIR%watchdog.vbs\"" /sc minute /mo 1 /f >nul

echo [OK] 已设置：
echo   ① 开机自启动  ：注册表 HKCU\...\Run\PCGuardian
echo   ② 看门狗      ：计划任务 PCGuardianWatch（每 1 分钟自检复活）
echo   ③ 进程伪装    ：sysguard.exe（任务管理器里不易辨认）
echo.
echo 程序位置 : %DIR%
echo.
echo 下次开机登录后，电脑将自动锁定，直到完成
echo 「语文/英语阅读」和「背单词」打卡（家长在网页后台打卡）。
echo 孩子在任务管理器里结束进程也没用，1 分钟内自动恢复（剩余时长不重置）。
echo.
echo 卸载请运行：卸载-开机自启.bat
echo 立即试运行：PCGuardian.exe --demo（测试模式，Esc 退出，不锁定不关机）
echo.
pause
