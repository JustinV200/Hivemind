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
$TailscaleSigner = '^CN=Tailscale Inc\.,'   # The MSI's Authenticode subject must start with this.

function Say([string]$Text) { Write-Host "pollinate: $Text" }

# Run a native program with its error output dropped; Windows PowerShell 5.1 would otherwise turn
# any line it writes to stderr into a terminating error under 'Stop'.
function Invoke-Quiet([scriptblock]$Block) {
    $ErrorActionPreference = 'Continue'
    $output = & $Block 2>$null | Out-String
    return @{ Code = $LASTEXITCODE; Output = $output }
}

# The same, keeping what the program wrote to stderr, for an error message worth reading.
function Invoke-Captured([scriptblock]$Block) {
    $ErrorActionPreference = 'Continue'
    $output = & $Block 2>&1 | ForEach-Object { "$_" } | Out-String
    return @{ Code = $LASTEXITCODE; Output = $output.Trim() }
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
    # Hold the file open, shared for reading only, from the checks until msiexec is done with it:
    # nothing else in %TEMP% can swap or rewrite it between being checked and being installed.
    $lock = [IO.File]::Open($msi, 'Open', 'Read', 'Read')
    try {
        $signature = Get-AuthenticodeSignature $msi
        # Both checks or nothing runs: the hash Tailscale published, and a valid signature whose
        # subject is Tailscale Inc.'s own (anchored, so 'NotTailscale' or 'O=Tailscale' cannot pass).
        if ((Get-FileHash -Algorithm SHA256 $msi).Hash -ne $expected -or $signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch $TailscaleSigner) {
            throw 'The Tailscale MSI failed its hash or signature check; it was not installed.'
        }
        # A machine-wide install needs elevation, and /quiet shows no UAC prompt of its own.
        try { $process = Start-Process msiexec.exe -Verb RunAs -ArgumentList @('/i', "`"$msi`"", '/quiet', '/norestart') -Wait -PassThru }
        catch { throw 'Installing Tailscale needs administrator rights, and they were not given.' }
    }
    finally { $lock.Dispose(); Remove-Item -Force $msi -ErrorAction SilentlyContinue }
    if ($process.ExitCode -ne 0 -and $process.ExitCode -ne 3010) { throw "The Tailscale MSI failed (exit $($process.ExitCode))." }
}

function Get-TailscaleStatus {
    $result = Invoke-Quiet { & $TailscaleExe status --json }
    if ($result.Code -ne 0 -or -not $result.Output.Trim()) { return $null }
    try { return ($result.Output | ConvertFrom-Json) } catch { return $null }
}

# Join the tailnet with the packet's single-use key, read from its file so it is never on a
# command line. Unattended: Tailscale stays connected with nobody logged in and reconnects by
# itself on any network.
function Join-Tailnet {
    # Skipped only when this is the packet's own tagged node, up (a re-run); any other signed-in
    # account was refused by Test-OtherTailnet before the run began.
    $status = Get-TailscaleStatus
    if ($status -and $status.BackendState -eq 'Running' -and (Test-HiveTagged $status)) { Say 'This PC is already on the tailnet.'; return }
    if (-not (Test-Path $AuthKeyFile)) { throw 'This packet has been used: its Tailscale key is gone. Ask for a new packet.' }
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    while ((Get-Service Tailscale -ErrorAction SilentlyContinue).Status -ne 'Running') {
        if ((Get-Date) -gt $deadline) { throw 'The Tailscale service did not start.' }
        Start-Sleep -Seconds 2
    }
    Say 'Joining the tailnet...'
    # The key is passed as a file: path, so tailscale's own error text cannot contain it.
    $result = Invoke-Captured { & $TailscaleExe up "--auth-key=file:$AuthKeyFile" --unattended --reset "--timeout=$($WaitSeconds)s" }
    Remove-Item -Force $AuthKeyFile   # Spent or refused, it is of no further use.
    if ($result.Code -ne 0) { throw "tailscale up failed (exit $($result.Code)): $($result.Output)" }
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
        if ((Get-Date) -gt $deadline) {
            throw "The Hive Stand ${HiveHost}:$HivePort does not resolve or answer over the tailnet. Is the Hive running, and does the tailnet policy let tag:hive-device reach it?"
        }
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

# Signed in already, but not as a Hive device: another Tailscale account. Checked before anything
# can spend or delete the secrets, so the same packet works after `tailscale logout`.
function Test-HiveTagged($Status) {
    return ($Status.Self -and (@($Status.Self.Tags) -contains 'tag:hive-device'))
}

# Signed in means any state but logged out, connected or not (Stopped counts), or a tailnet named.
function Test-OtherTailnet {
    if (-not (Test-Path $TailscaleExe)) { return $false }
    $status = Get-TailscaleStatus
    if (-not $status) { return $false }
    $signedIn = ($status.BackendState -notin @('NeedsLogin', 'NoState', '')) -or $status.CurrentTailnet
    return ($signedIn -and -not (Test-HiveTagged $status))
}

# Delete the secrets, retrying once (an antivirus scan can hold a file for a moment); say so
# loudly if they are still there, since they stay a key to the Hive until they lapse.
function Remove-PacketSecrets {
    if (-not (Test-Path $Secrets)) { return }
    foreach ($attempt in 1, 2) {
        try { Remove-Item -Recurse -Force $Secrets -ErrorAction Stop; Say "Deleted this packet's secrets."; return }
        catch { Start-Sleep -Seconds 1 }
    }
    Write-Host "pollinate: secrets\ could NOT be deleted; delete it now: $Secrets" -ForegroundColor Red
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

if (Test-OtherTailnet) {
    Write-Host "pollinate: STOPPED: this PC is signed in to another Tailscale account. Run 'tailscale logout', then run this script again. Nothing was used up." -ForegroundColor Red
    exit 1
}
$failed = $false
try { Invoke-Pollinate }
catch { $failed = $true; Write-Host "pollinate: FAILED: $($_.Exception.Message)" -ForegroundColor Red }
finally { Remove-PacketSecrets }
if ($failed) { exit 1 }
Say 'Done: this PC is on the tailnet, enrolled, and pollen starts at every login.'
