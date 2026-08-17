# Start the Arbiter stack: MinIO (object store) + a HydraDB graph node.
#   powershell -File scripts\hydradb_up.ps1
#
# Why MinIO: HydraDB stores everything in S3-compatible object storage. The
# CLOUD_PROVIDER=local backend works for a handful of writes and then fails with
#   "Operation `put_opts` with mode `PutMode::Update` not yet implemented by
#    LocalFileSystem"
# because SlateDB needs conditional puts for the writer lease. Any real ingest
# needs an S3-compatible store, so local dev runs MinIO.
#
# Ports: 7687 Bolt, 8443 HTTP, 9090 admin, 9000 MinIO S3, 9001 MinIO console.

$ErrorActionPreference = 'Stop'
$root   = Split-Path -Parent $PSScriptRoot
$data   = Join-Path $root '.hydradb-data'
$net    = 'arbiter-net'
$bucket = 'hydradb'

foreach ($d in @($data, (Join-Path $data 'cache'))) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
}
$tokenFile = Join-Path $data 'auth-token'
if (-not (Test-Path $tokenFile)) {
    [System.IO.File]::WriteAllText($tokenFile, "local-development-token-32-bytes`n")
}

docker network create $net 2>$null | Out-Null
docker volume create arbiter-minio-data | Out-Null

# --- MinIO ------------------------------------------------------------------
# Uses a docker volume, not a Windows bind mount: MinIO misreports free space on
# a bind mount and rejects every write with 507 Insufficient Storage.
docker rm -f arbiter-minio 2>$null | Out-Null
docker run -d --name arbiter-minio --network $net -p 9000:9000 -p 9001:9001 `
    -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin `
    -v arbiter-minio-data:/data `
    quay.io/minio/minio server /data --console-address ":9001" | Out-Null

$ready = $false
foreach ($i in 1..30) {
    Start-Sleep -Seconds 2
    try {
        Invoke-WebRequest -Uri 'http://127.0.0.1:9000/minio/health/live' -UseBasicParsing -TimeoutSec 3 | Out-Null
        $ready = $true; break
    } catch { }
}
if (-not $ready) { Write-Host 'MinIO did not become healthy; check: docker logs arbiter-minio' -ForegroundColor Red; exit 1 }

docker run --rm --network $net --entrypoint sh quay.io/minio/mc -c `
    "mc alias set local http://arbiter-minio:9000 minioadmin minioadmin > /dev/null && mc mb -p local/$bucket > /dev/null && echo bucket-ready" | Out-Null

# --- HydraDB ----------------------------------------------------------------
docker rm -f arbiter-hydradb 2>$null | Out-Null
docker run -d --name arbiter-hydradb --network $net `
    -p 7687:7687 -p 8443:8443 -p 9090:9090 `
    -v "${data}:/data" `
    -e CLOUD_PROVIDER=aws `
    -e AWS_BUCKET_NAME=$bucket -e AWS_BUCKET=$bucket `
    -e AWS_REGION=us-east-1 -e AWS_DEFAULT_REGION=us-east-1 `
    -e AWS_ENDPOINT=http://arbiter-minio:9000 -e AWS_ENDPOINT_URL=http://arbiter-minio:9000 `
    -e AWS_ALLOW_HTTP=true `
    -e AWS_ACCESS_KEY_ID=minioadmin -e AWS_SECRET_ACCESS_KEY=minioadmin `
    -e GRAPH_NAMESPACE=default -e GRAPH_ID=default `
    -e GRAPH_CELL_ID=cell-0 -e GRAPH_CELLS=cell-0 -e GRAPH_NODE_ID=node-0 `
    -e GRAPH_BOLT_NODE_ADDRESSES=node-0=127.0.0.1:7687 `
    -e GRAPH_ADVERTISED_BOLT_ADDR=127.0.0.1:7687 `
    -e GRAPH_DATA_CACHE_DIR=/data/cache `
    -e GRAPH_AUTH_TOKEN_FILE=/data/auth-token `
    -e GRAPH_ALLOW_PLAINTEXT=true -e RUST_MIN_STACK=33554432 `
    ghcr.io/hydra-db/hydradb:latest | Out-Null

# Wait for the query endpoint to answer, not just for the container to exist.
$ready = $false
foreach ($i in 1..30) {
    Start-Sleep -Seconds 2
    try {
        $r = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8443/v1/graphs/default/query' `
            -Headers @{ Authorization = 'Bearer local-development-token-32-bytes'; 'X-Graph-Namespace' = 'default' } `
            -ContentType 'application/json' `
            -Body '{"cell_id":"cell-0","query":"MATCH (n:_Health) RETURN n.id AS id LIMIT 1"}'
        if ($null -ne $r) { $ready = $true; break }
    } catch { }
}

if ($ready) {
    Write-Host "Arbiter stack ready - bolt 7687 / http 8443 / admin 9090 / minio console 9001" -ForegroundColor Green
} else {
    Write-Host "HydraDB did not become ready; check: docker logs arbiter-hydradb" -ForegroundColor Red
    Write-Host "A 507 Insufficient Storage in the logs means the Docker VM is out of disk." -ForegroundColor Yellow
    exit 1
}
