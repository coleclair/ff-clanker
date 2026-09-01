<#
  FF Draft Assistant - one-click installer / updater for Windows.

  You normally do NOT run this file directly: Windows PowerShell blocks
  unsigned scripts by default, which is the "enter a special argument" nag.
  Instead double-click one of the batch files, which launch this with
  -ExecutionPolicy Bypass so nothing is ever blocked:

      install.bat  - fresh PC: downloads the whole app, then sets it up.
      setup.bat    - already have the folder: just sets up + launches.

  This script is safe to run again any time - it updates the app in place.
#>

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# ---- settings ---------------------------------------------------------------
$Repo    = 'https://github.com/coleclair/ff-clanker.git'
$ZipUrl  = 'https://github.com/coleclair/ff-clanker/archive/refs/heads/main.zip'
$ZipRoot = 'ff-clanker-main'          # top folder name inside the GitHub zip
$AppName = 'FF Draft Assistant'
$Entry   = 'ffdraft.py'

# ---- pretty output ----------------------------------------------------------
function Say  ($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Ok   ($m) { Write-Host "    $m" -ForegroundColor Green }
function Warn ($m) { Write-Host "    $m" -ForegroundColor Yellow }

function Refresh-Path {
    # Pick up PATH changes made by an installer without reopening the shell.
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user    = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ';'
}

# ---- find or install Python -------------------------------------------------
function Get-Python {
    # Prefer the 'py' launcher (most reliable on Windows).
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -c "import sys" *> $null
        if ($LASTEXITCODE -eq 0) {
            return [pscustomobject]@{ Cmd = 'py'; Pre = @('-3') }
        }
    }
    # Fall back to python.exe, but skip the Microsoft Store stub.
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python -and $python.Source -notmatch 'WindowsApps') {
        return [pscustomobject]@{ Cmd = $python.Source; Pre = @() }
    }
    return $null
}

function Install-Python {
    Say "Python isn't installed - installing it (one-time)..."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id Python.Python.3.12 -e --source winget --scope user `
            --accept-package-agreements --accept-source-agreements
    }
    else {
        Warn "winget not available - downloading the official Python installer..."
        $inst = Join-Path $env:TEMP 'ffdraft-python-setup.exe'
        Invoke-WebRequest -UseBasicParsing `
            -Uri 'https://www.python.org/ftp/python/3.12.6/python-3.12.6-amd64.exe' `
            -OutFile $inst
        Start-Process -Wait -FilePath $inst -ArgumentList `
            '/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_pip=1'
        Remove-Item $inst -Force -ErrorAction SilentlyContinue
    }
    Refresh-Path
}

# ---- download / update the app ----------------------------------------------
function Ensure-App ($Dir) {
    $git = Get-Command git -ErrorAction SilentlyContinue

    if (Test-Path (Join-Path $Dir $Entry)) {
        if ($git -and (Test-Path (Join-Path $Dir '.git'))) {
            Say "Updating to the latest version..."
            git -C $Dir pull --ff-only
        }
        return
    }

    if ($git) {
        Say "Downloading the app (git clone)..."
        git clone --depth 1 $Repo $Dir
        return
    }

    Say "Downloading the app..."
    $zip = Join-Path $env:TEMP 'ffdraft.zip'
    $tmp = Join-Path $env:TEMP 'ffdraft_unzip'
    Invoke-WebRequest -UseBasicParsing -Uri $ZipUrl -OutFile $zip
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    # Merge the extracted files into $Dir (keeps any local cache/settings).
    robocopy (Join-Path $tmp $ZipRoot) $Dir /E /NFL /NDL /NJH /NJS /NC /NS /NP *> $null
    Remove-Item $zip, $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

# ---- install dependencies ---------------------------------------------------
function Install-Deps ($P, $Dir) {
    Say "Installing Python packages (requests, truststore)..."
    $req = Join-Path $Dir 'requirements.txt'

    & $P.Cmd @($P.Pre + @('-m', 'pip', 'install', '--upgrade', 'pip')) *> $null

    & $P.Cmd @($P.Pre + @('-m', 'pip', 'install', '-r', $req))
    if ($LASTEXITCODE -ne 0) {
        Warn "Retrying package install into your user profile..."
        & $P.Cmd @($P.Pre + @('-m', 'pip', 'install', '--user', '-r', $req))
        if ($LASTEXITCODE -ne 0) { throw "Could not install required Python packages." }
    }
}

# ---- desktop / start-menu shortcut ------------------------------------------
function Get-Pythonw ($P) {
    $exe = (& $P.Cmd @($P.Pre + @('-c', 'import sys; print(sys.executable)'))).Trim()
    $pyw = Join-Path (Split-Path $exe) 'pythonw.exe'
    if (Test-Path $pyw) { return $pyw }
    return $exe   # falls back to python.exe (a console will flash briefly)
}

function Make-Shortcuts ($Dir, $Pythonw) {
    $ico   = Join-Path $Dir 'football.ico'
    $entry = Join-Path $Dir $Entry
    $wsh   = New-Object -ComObject WScript.Shell

    $targets = @(
        [Environment]::GetFolderPath('Desktop'),
        (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs')
    )
    foreach ($loc in $targets) {
        if (-not (Test-Path $loc)) { continue }
        $lnk = $wsh.CreateShortcut((Join-Path $loc "$AppName.lnk"))
        $lnk.TargetPath       = $Pythonw
        $lnk.Arguments        = '"' + $entry + '"'
        $lnk.WorkingDirectory = $Dir
        $lnk.Description       = $AppName
        if (Test-Path $ico) { $lnk.IconLocation = $ico }
        $lnk.Save()
    }
}

# ---- main -------------------------------------------------------------------
try {
    Write-Host ""
    Write-Host "  $AppName - setup" -ForegroundColor White
    Write-Host "  --------------------------" -ForegroundColor DarkGray

    # If this script sits next to ffdraft.py, install right here; otherwise
    # (e.g. run straight from the web) drop the app in the user's home folder.
    if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot $Entry))) {
        $Dir = $PSScriptRoot
    }
    else {
        $Dir = Join-Path $HOME 'ff-clanker'
    }

    $P = Get-Python
    if (-not $P) { Install-Python; $P = Get-Python }
    if (-not $P) {
        throw "Python still not found. Install Python 3.10+ from " +
              "https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), " +
              "then run this again."
    }

    Ensure-App    $Dir
    Install-Deps  $P $Dir
    $Pythonw = Get-Pythonw $P
    Make-Shortcuts $Dir $Pythonw

    Write-Host ""
    Ok "Installed to:  $Dir"
    Ok "Shortcut added to your Desktop and Start Menu: '$AppName'"
    Say "Launching the app..."
    Start-Process -FilePath $Pythonw -ArgumentList ('"' + (Join-Path $Dir $Entry) + '"') `
        -WorkingDirectory $Dir

    Write-Host ""
    Ok "All done! Next time, just double-click the '$AppName' shortcut."
}
catch {
    Write-Host ""
    Write-Host "Setup failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "If it's a network issue, check your connection and run it again." -ForegroundColor Red
}
finally {
    if ($Host.Name -eq 'ConsoleHost') {
        Read-Host "`nPress Enter to close this window" | Out-Null
    }
}
