# pollinate.ps1: put this Windows PC on the Hive's private network, enrol it in the Swarm and
# start pollen at every login. Written by `hive swarm packet` for @@HIVE_HOST@@.
#
# Run it from the packet folder, as the account pollen should run under:
#     powershell -ExecutionPolicy Bypass -File .\pollinate.ps1
#
# Safe to run again: each step checks whether it is already done. The invite and the Tailscale
# key in .\secrets are never printed, are handed to the programs that use them by file or
# standard input (never on a command line), and are deleted when this script ends, whether it
# worked or not.

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 1

$HiveHost = @@HIVE_HOST_QUOTED@@         # The Hive Stand's name on the tailnet (MagicDNS).
$HivePort = @@HIVE_PORT@@             # The swarm listener's port.
$Secrets = Join-Path $PSScriptRoot 'secrets'
$AuthKeyFile = Join-Path $Secrets 'tailscale-authkey.txt'
$InviteFile = Join-Path $Secrets 'invite.txt'
$InstallDir = Join-Path $env:LOCALAPPDATA 'Programs\pollen'   # Per user: no admin rights needed.
$Pollen = Join-Path $InstallDir 'pollen.exe'
$TailscaleExe = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
$MsiIndex = 'https://pkgs.tailscale.com/stable/'               # Tailscale's official packages.
$WaitSeconds = 120                    # How long to wait for Tailscale and for the Hive Stand.

function Say([string]$Text) { Write-Host "pollinate: $Text" }

# Run a native program with its error output dropped; Windows PowerShell 5.1 would otherwise turn
# any line it writes to stderr into a terminating error under 'Stop'.
function Invoke-Quiet([scriptblock]$Block) {
    $ErrorActionPreference = 'Continue'
    $output = & $Block 2>$null | Out-String
    return @{ Code = $LASTEXITCODE; Output = $output }
}

# Tailscale: winget first (signed by the Windows package source), else the official MSI, checked
# against its published SHA-256 and its Authenticode signature before it runs.
function Install-Tailscale {
    if (Test-Path $TailscaleExe) { Say 'Tailscale is already installed.'; return }
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Say 'Installing Tailscale with winget (Windows may ask to allow it)...'
        $null = Invoke-Quiet { winget install --id Tailscale.Tailscale --exact --silent --accept-package-agreements --accept-source-agreements }
    }
    if (-not (Test-Path $TailscaleExe)) { Install-TailscaleMsi }
    if (-not (Test-Path $TailscaleExe)) { throw 'Tailscale did not install.' }
}

function Install-TailscaleMsi {
    Say 'Installing Tailscale from its official MSI (Windows may ask to allow it)...'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $name = (Invoke-RestMethod -UseBasicParsing -Uri ($MsiIndex + '?mode=json')).MSIs.amd64
    if (-not $name) { throw 'pkgs.tailscale.com did not name a Tailscale MSI.' }
    $msi = Join-Path $env:TEMP $name
    Invoke-WebRequest -UseBasicParsing -Uri ($MsiIndex + $name) -OutFile $msi
    $published = (Invoke-WebRequest -UseBasicParsing -Uri ($MsiIndex + $name + '.sha256')).Content
    if ($published -is [byte[]]) { $published = [Text.Encoding]::ASCII.GetString($published) }
    $expected = ($published.Trim() -split '\s+')[0]
    $signature = Get-AuthenticodeSignature $msi
    # Both checks or nothing runs: the hash Tailscale published, and Tailscale's own signature.
    if ((Get-FileHash -Algorithm SHA256 $msi).Hash -ne $expected -or $signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Tailscale') {
        Remove-Item -Force $msi
        throw 'The Tailscale MSI failed its hash or signature check; it was not installed.'
    }
    $process = Start-Process msiexec.exe -ArgumentList @('/i', "`"$msi`"", '/quiet', '/norestart') -Wait -PassThru
    Remove-Item -Force $msi
    if ($process.ExitCode -ne 0 -and $process.ExitCode -ne 3010) { throw "The Tailscale MSI failed (exit $($process.ExitCode))." }
}

function Get-TailscaleState {
    $result = Invoke-Quiet { & $TailscaleExe status --json }
    if ($result.Code -ne 0 -or -not $result.Output.Trim()) { return '' }
    try { return ($result.Output | ConvertFrom-Json).BackendState } catch { return '' }
}

# Join the tailnet with the packet's single-use key, read from its file so it is never on a
# command line. Unattended: Tailscale stays connected with nobody logged in and reconnects by
# itself on any network.
function Join-Tailnet {
    if ((Get-TailscaleState) -eq 'Running') { Say 'This PC is already on the tailnet.'; return }
    if (-not (Test-Path $AuthKeyFile)) { throw 'This packet has been used: its Tailscale key is gone. Ask for a new packet.' }
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    while ((Get-Service Tailscale -ErrorAction SilentlyContinue).Status -ne 'Running') {
        if ((Get-Date) -gt $deadline) { throw 'The Tailscale service did not start.' }
        Start-Sleep -Seconds 2
    }
    Say 'Joining the tailnet...'
    $result = Invoke-Quiet { & $TailscaleExe up "--auth-key=file:$AuthKeyFile" --unattended --reset "--timeout=$($WaitSeconds)s" }
    Remove-Item -Force $AuthKeyFile   # Spent or refused, it is of no further use.
    if ($result.Code -ne 0) { throw "tailscale up failed (exit $($result.Code))." }
}

function Test-HiveStand {
    try { $null = [Net.Dns]::GetHostAddresses($HiveHost) } catch { return $false }
    $client = New-Object Net.Sockets.TcpClient
    try { return ($client.ConnectAsync($HiveHost, $HivePort).Wait(3000) -and $client.Connected) }
    catch { return $false }
    finally { $client.Dispose() }
}

function Wait-HiveStand {
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    while (-not (Test-HiveStand)) {
        if ((Get-Date) -gt $deadline) { throw "The Hive Stand ${HiveHost}:$HivePort does not resolve or answer over the tailnet. Is the Hive running, and does the tailnet policy let tag:hive-device reach it?" }
        Start-Sleep -Seconds 3
    }
    Say "The Hive Stand answers at ${HiveHost}:$HivePort."
}

function Install-Pollen {
    $source = Join-Path $PSScriptRoot 'pollen.exe'
    if ((Test-Path $Pollen) -and (Get-FileHash $Pollen).Hash -eq (Get-FileHash $source).Hash) { Say 'pollen is already installed.'; return }
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Copy-Item -Force $source $Pollen
    Say "Installed pollen to $Pollen."
}

# Enrol with the packet's single-use invite, given on standard input so it never appears in a
# process list. A device that is already enrolled is left as it is.
function Register-Pollen {
    if ((Invoke-Quiet { & $Pollen status }).Code -eq 0) { Say 'This PC is already enrolled.'; return }
    if (-not (Test-Path $InviteFile)) { throw 'This packet has been used: its invite is gone. Ask for a new packet.' }
    Get-Content -Raw $InviteFile | & $Pollen enrol -
    if ($LASTEXITCODE -ne 0) { throw "pollen enrol failed (exit $LASTEXITCODE)." }
}

function Invoke-Pollinate {
    Install-Tailscale
    Join-Tailnet
    Wait-HiveStand
    Install-Pollen
    Register-Pollen
    & $Pollen install
    if ($LASTEXITCODE -ne 0) { throw "pollen install failed (exit $LASTEXITCODE)." }
    & $Pollen status
}

$failed = $false
try { Invoke-Pollinate }
catch { $failed = $true; Write-Host "pollinate: FAILED: $($_.Exception.Message)" -ForegroundColor Red }
finally {
    if (Test-Path $Secrets) { Remove-Item -Recurse -Force $Secrets; Say "Deleted this packet's secrets." }
}
if ($failed) { exit 1 }
Say 'Done: this PC is on the tailnet, enrolled, and pollen starts at every login.'
