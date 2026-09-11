param(
    [string]$Commit = "82ce04e611d19a12556c3955125b048b36187f52"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$upstreamRoot = Join-Path $projectRoot "data\raw\upstream"
$target = Join-Path $upstreamRoot "pokemon-dataset-zh"
$dataTarget = Join-Path $target "data"

New-Item -ItemType Directory -Path $dataTarget -Force | Out-Null
$resolvedUpstream = (Resolve-Path $upstreamRoot).Path
if (-not $target.StartsWith($resolvedUpstream, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to write outside the project upstream directory: $target"
}

$rawBase = "https://raw.githubusercontent.com/42arch/pokemon-dataset-zh/$Commit"
foreach ($filename in @("ability_list.json", "move_list.json", "item_list.json")) {
    Invoke-WebRequest -Uri "$rawBase/data/$filename" -OutFile (Join-Path $dataTarget $filename)
}
Invoke-WebRequest -Uri "$rawBase/LICENSE" -OutFile (Join-Path $target "LICENSE")
Invoke-WebRequest `
    -Uri "https://api.github.com/repos/42arch/pokemon-dataset-zh/commits/$Commit" `
    -OutFile (Join-Path $target "commit.json")

$metadata = Get-Content -Raw -LiteralPath (Join-Path $target "commit.json") | ConvertFrom-Json
if ($metadata.sha -ne $Commit) {
    throw "Commit verification failed. Expected $Commit, got $($metadata.sha)"
}
Write-Output "Chinese text snapshot ready at $target ($Commit)"
