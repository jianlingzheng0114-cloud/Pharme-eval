@echo off
chcp 65001 >nul
rem =====================================================
rem  PHARM-E Prompt Element Evaluation Tool - Launcher
rem  Usage: double-click this file. Keep it inside the
rem  tool folder (it locates itself via %~dp0).
rem  Requires: Python 3.10+ in PATH, deps from requirements.txt
rem =====================================================
title PHARM-E Evaluation Tool

set "TOOL_DIR=%~dp0"

rem 可用环境变量 PHARME_PYTHON 指定 Python 解释器完整路径
if defined PHARME_PYTHON (
    set "PYTHON=%PHARME_PYTHON%"
) else (
    set "PYTHON=python"
)

where %PYTHON% >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Python（%PYTHON%）。
    echo        请安装 Python 3.10+ 并加入 PATH，
    echo        或设置环境变量 PHARME_PYTHON 指向解释器完整路径。
    pause
    exit /b 1
)

if not exist "%TOOL_DIR%app.py" (
    echo [错误] 未找到 app.py。
    echo        请把本 bat 文件放在工具文件夹内再双击。
    pause
    exit /b 1
)

if not exist "%TOOL_DIR%config.json" (
    echo [提示] 未找到 config.json，请先复制 config.example.json 为 config.json 并填入 API Key。
    pause
    exit /b 1
)

cd /d "%TOOL_DIR%"
echo ============================================
echo   PHARM-E 提示词要素测评工具 启动中...
echo   浏览器将自动打开 http://127.0.0.1:7860
echo   请保持本窗口打开；关闭窗口即停止工具。
echo ============================================
%PYTHON% "%TOOL_DIR%app.py"

echo.
if errorlevel 1 (
    echo [错误] 程序异常退出（错误码 %errorlevel%），请截图反馈。
) else (
    echo [完成] 工具已退出。
)
pause
