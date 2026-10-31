# Windows: installs git, ffmpeg and uv if they're missing, then runs `yt-jp-redub setup`,
# which downloads IndexTTS, the models and yt-dlp. Safe to run again.
# Run it with:  powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
    [Environment]::GetEnvironmentVariable("Path", "User")
}

foreach ($tool in @(@("git", "Git.Git"), @("ffmpeg", "Gyan.FFmpeg"))) {
  if (Get-Command $tool[0] -ErrorAction SilentlyContinue) { continue }
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host "Install $($tool[0]) (winget isn't available here), then run this again."
    exit 1
  }
  Write-Host "Installing $($tool[0])..."
  winget install --id $tool[1] -e --accept-source-agreements --accept-package-agreements
  Refresh-Path
  if (-not (Get-Command $tool[0] -ErrorAction SilentlyContinue)) {
    Write-Host "$($tool[0]) still isn't on PATH. Open a new terminal and run this again."
    exit 1
  }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "Installing uv..."
  Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
  Refresh-Path
}

uv run yt-jp-redub setup
exit $LASTEXITCODE
