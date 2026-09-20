param(
    [string]$Commit = "d849b220082e113d8a17303509fb44d420c543d5"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$upstreamRoot = Join-Path $projectRoot "data\raw\upstream"
$target = Join-Path $upstreamRoot "pokemon-showdown"

New-Item -ItemType Directory -Path $target -Force | Out-Null
$resolvedUpstream = (Resolve-Path $upstreamRoot).Path
$resolvedTarget = (Resolve-Path $target).Path
if (-not $resolvedTarget.StartsWith($resolvedUpstream, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to write outside the project upstream directory: $resolvedTarget"
}

$rawBase = "https://raw.githubusercontent.com/smogon/pokemon-showdown/$Commit"
Invoke-WebRequest -Uri "$rawBase/data/moves.ts" -OutFile (Join-Path $target "moves.ts")
Invoke-WebRequest -Uri "$rawBase/sim/dex-moves.ts" -OutFile (Join-Path $target "dex-moves.ts")
Invoke-WebRequest -Uri "$rawBase/data/abilities.ts" -OutFile (Join-Path $target "abilities.ts")
Invoke-WebRequest -Uri "$rawBase/data/conditions.ts" -OutFile (Join-Path $target "conditions.ts")
Invoke-WebRequest -Uri "$rawBase/sim/dex-abilities.ts" -OutFile (Join-Path $target "dex-abilities.ts")
Invoke-WebRequest -Uri "$rawBase/LICENSE" -OutFile (Join-Path $target "LICENSE")
try {
    Invoke-WebRequest `
        -Uri "https://api.github.com/repos/smogon/pokemon-showdown/commits/$Commit" `
        -OutFile (Join-Path $target "commit.json")
}
catch {
    $existingCommit = Join-Path $target "commit.json"
    if (-not (Test-Path -LiteralPath $existingCommit)) {
        throw
    }
    Write-Warning "GitHub commit API unavailable; retaining the existing pinned commit metadata."
}

$metadata = Get-Content -Raw -LiteralPath (Join-Path $target "commit.json") | ConvertFrom-Json
if ($metadata.sha -ne $Commit) {
    throw "Commit verification failed. Expected $Commit, got $($metadata.sha)"
}
Write-Output "Pokémon Showdown move/ability/battle-condition snapshot ready at $target ($Commit)"
