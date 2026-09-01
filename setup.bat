@echo off
rem ===================================================================
rem  FF Draft Assistant - one-click setup (run me from inside the folder)
rem
rem  Double-click this file. It installs Python (if needed), installs the
rem  app's dependencies, makes a Desktop shortcut, and launches the app.
rem
rem  Batch files are exempt from PowerShell's "unsigned script" block, and
rem  this passes -ExecutionPolicy Bypass for you, so nothing gets blocked.
rem ===================================================================
title FF Draft Assistant - Setup
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
