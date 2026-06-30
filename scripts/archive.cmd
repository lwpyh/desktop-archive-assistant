@echo off
REM 便捷入口：archive.cmd scan / archive.cmd rollback / archive.cmd organize %USERPROFILE%\Desktop
SETLOCAL
SET "SKILL_DIR=%~dp0.."
PUSHD "%SKILL_DIR%"
python -m archive_assistant.cli.main %*
SET "RC=%ERRORLEVEL%"
POPD
ENDLOCAL & EXIT /B %RC%
