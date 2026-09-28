$ErrorActionPreference = 'Continue'
$failed = $false

$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
$ffprobe = Get-Command ffprobe -ErrorAction SilentlyContinue

if (-not $ffmpeg) {
    Write-Host 'FAIL: ffmpeg was not found on PATH.'
    $failed = $true
} else {
    $filters = (& $ffmpeg.Source -hide_banner -filters 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'FAIL: ffmpeg could not list its filters.'
        $failed = $true
    } else {
        foreach ($name in @('drawtext', 'subtitles', 'scale', 'overlay')) {
            if ($filters -match "(?m)^\s*[A-Z.]{1,4}\s+$name\s") {
                Write-Host "OK: ffmpeg has the $name filter."
            } else {
                Write-Host "FAIL: ffmpeg is missing the $name filter."
                $failed = $true
            }
        }
    }
}

if (-not $ffprobe) {
    Write-Host 'FAIL: ffprobe was not found on PATH.'
    $failed = $true
} else {
    Write-Host "OK: ffprobe found at $($ffprobe.Source)."
}

if ($failed) {
    Write-Host 'Install an FFmpeg build with subtitle and drawtext support, add its bin folder to PATH, and open a new PowerShell window.'
    exit 1
}

Write-Host 'Tool checks passed. A real video render can still fail if fonts or codecs are unavailable.'
