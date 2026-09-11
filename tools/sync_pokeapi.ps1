param(
    [string]$Commit = "8dfd1e309d4a1ca11f10b185412ed7dc8dd2b310"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$upstreamRoot = Join-Path $projectRoot "data\raw\upstream"
$target = Join-Path $upstreamRoot "pokeapi"

New-Item -ItemType Directory -Path $upstreamRoot -Force | Out-Null
$resolvedUpstream = (Resolve-Path $upstreamRoot).Path
if (-not $target.StartsWith($resolvedUpstream, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to write outside the project upstream directory: $target"
}

if (-not (Test-Path -LiteralPath $target)) {
    git clone --depth 1 --filter=blob:none --sparse https://github.com/PokeAPI/pokeapi.git $target
}

git -c "safe.directory=$($target.Replace('\', '/'))" -C $target sparse-checkout set data/v2/csv
git -c "safe.directory=$($target.Replace('\', '/'))" -C $target fetch --depth 1 origin $Commit
git -c "safe.directory=$($target.Replace('\', '/'))" -C $target checkout --detach $Commit

$actual = git -c "safe.directory=$($target.Replace('\', '/'))" -C $target rev-parse HEAD
if ($actual -ne $Commit) {
    throw "Commit verification failed. Expected $Commit, got $actual"
}
Write-Output "PokéAPI CSV snapshot ready at $target ($actual)"
