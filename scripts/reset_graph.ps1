# Wipe the graph and start clean.
#   powershell -File scripts\reset_graph.ps1
#
# The engine has no DELETE and no MERGE on relationships, so re-running the
# loader against an existing graph would duplicate every edge. Resetting means
# emptying the object store the graph lives in, then restarting the node.
#
# This verifies the wipe rather than assuming it. Removing a bucket holding
# ~1 GB can fail or partially complete, and a reset that silently does nothing
# leaves the previous corpus in place — which looks like a catastrophic
# accuracy regression rather than a failed delete.

$ErrorActionPreference = 'Stop'
$net = 'arbiter-net'
$root = Split-Path -Parent $PSScriptRoot

function Invoke-Mc([string]$Script) {
    docker run --rm --network $net --entrypoint sh quay.io/minio/mc -c `
        "mc alias set l http://arbiter-minio:9000 minioadmin minioadmin > /dev/null && $Script"
}

# Stop the writer first: deleting objects under a live node races its WAL.
docker stop arbiter-hydradb | Out-Null

Invoke-Mc "mc rm --recursive --force --quiet l/hydradb > /dev/null 2>&1; mc rb --force l/hydradb > /dev/null 2>&1; mc mb -p l/hydradb > /dev/null; echo wiped" | Out-Null

$remaining = (Invoke-Mc "mc ls --recursive l/hydradb | wc -l").Trim()
if ($remaining -ne '0') {
    Write-Host "reset FAILED - $remaining objects still in the bucket" -ForegroundColor Red
    docker start arbiter-hydradb | Out-Null
    exit 1
}

# The node caches store data locally; a stale cache outlives the wipe.
$cache = Join-Path $root '.hydradb-data\cache'
if (Test-Path $cache) { Remove-Item -Recurse -Force "$cache\*" -ErrorAction SilentlyContinue }

docker start arbiter-hydradb | Out-Null

$ready = $false
foreach ($i in 1..40) {
    Start-Sleep -Seconds 2
    try {
        Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8443/v1/graphs/default/query' `
            -Headers @{ Authorization = 'Bearer local-development-token-32-bytes'; 'X-Graph-Namespace' = 'default' } `
            -ContentType 'application/json' `
            -Body '{"cell_id":"cell-0","query":"MATCH (n:_Health) RETURN n.id AS id LIMIT 1"}' | Out-Null
        $ready = $true; break
    } catch { }
}

if (-not $ready) {
    Write-Host 'node did not come back; check: docker logs arbiter-hydradb' -ForegroundColor Red
    exit 1
}

# Confirm the graph really is empty before declaring success.
$check = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8443/v1/graphs/default/query' `
    -Headers @{ Authorization = 'Bearer local-development-token-32-bytes'; 'X-Graph-Namespace' = 'default' } `
    -ContentType 'application/json' `
    -Body '{"cell_id":"cell-0","query":"MATCH (n:Claim) RETURN count(*) AS c"}'
$count = 0
if ($check.rows -and $check.rows.Count -gt 0) { $count = $check.rows[0][0].value }

if ($count -ne 0) {
    Write-Host "reset FAILED - $count claims still present" -ForegroundColor Red
    exit 1
}

Write-Host 'graph reset (verified empty) - run: python -m ingest.load' -ForegroundColor Green
