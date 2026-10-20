# Copy one command only. Do not open a COM port or send serial data.
param(
    [string]$Path = (Join-Path $PSScriptRoot 'serial_commands.txt'),
    [int]$Step = 0,
    [switch]$NoClipboard
)
$ErrorActionPreference = 'Stop'
$commands = [System.IO.File]::ReadAllLines((Resolve-Path -LiteralPath $Path).Path, [System.Text.Encoding]::UTF8)
if ($Step -eq 0) {
    Write-Output "TOTAL_STEPS=$($commands.Length)"
    Write-Output 'Specify -Step 1, then send one step at a time and wait for the Linux prompt.'
    exit 0
}
if ($Step -lt 1 -or $Step -gt $commands.Length) { throw 'Step out of range' }
$command = $commands[$Step - 1]
if ($command -match '[^\x00-\x7f]' -or $command.Length -ge 3800 -or $command.Contains("`r") -or $command.Contains("`n")) {
    throw 'Expected one ASCII command shorter than 3800 bytes'
}
if ($NoClipboard) { Write-Output $command }
else { Set-Clipboard -Value $command; Write-Output "COPIED=YES STEP=$Step/$($commands.Length) BYTES=$($command.Length)" }
