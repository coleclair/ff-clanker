@echo off
rem ===================================================================
rem  FF Draft Assistant - from-scratch installer for a fresh PC.
rem
rem  This is the ONLY file you need to download. Put it anywhere (Desktop
rem  is fine) and double-click it. It downloads the whole app, installs
rem  Python if it's missing, sets everything up, and launches it.
rem
rem  It re-downloads setup.ps1 from GitHub and runs it with
rem  -ExecutionPolicy Bypass, so PowerShell never blocks anything and you
rem  never have to type a special argument.
rem ===================================================================
title FF Draft Assistant - Installer
echo.
echo  Setting up FF Draft Assistant...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; iex (irm 'https://raw.githubusercontent.com/coleclair/ff-clanker/main/setup.ps1')"
