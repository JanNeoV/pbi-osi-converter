param(
    [Parameter(Mandatory=$true)][int]$Port,
    [Parameter(Mandatory=$true)][string]$QueryJson,
    [Parameter(Mandatory=$true)][string]$OutputFile,
    [string]$ClientDll
)
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $OutputFile) { throw 'Use a fresh output file; captures are never overwritten.' }
if (-not $ClientDll) {
    $package = Get-AppxPackage '*PowerBI*'
    if (@($package).Count -ne 1) { throw 'Supply -ClientDll for the installed Desktop ADOMD.NET library.' }
    $ClientDll = Join-Path $package.InstallLocation 'bin\Microsoft.PowerBI.AdomdClient.dll'
}
[void][Reflection.Assembly]::LoadFrom($ClientDll)
$queries = (Get-Content -LiteralPath $QueryJson -Raw | ConvertFrom-Json).queries
if (-not $queries) { throw 'QueryJson must contain a queries array with name and query fields.' }
$connection = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection("Data Source=localhost:$Port")
$result = [ordered]@{utc=[DateTime]::UtcNow.ToString('o'); clientFileVersion=(Get-Item -LiteralPath $ClientDll).VersionInfo.FileVersion; queries=@()}
try {
    $connection.Open()
    $result.serverVersion = $connection.ServerVersion
    foreach ($query in $queries) {
        $entry = [ordered]@{name=$query.name; query=$query.query; status='NOT_EXECUTED'}
        $reader = $null
        try {
            $command = $connection.CreateCommand()
            $command.CommandText = $query.query
            $reader = $command.ExecuteReader()
            $columns = @(); $rows = @()
            for ($i=0; $i -lt $reader.FieldCount; $i++) { $columns += $reader.GetName($i) }
            while ($reader.Read()) {
                $row = [ordered]@{}
                for ($i=0; $i -lt $reader.FieldCount; $i++) {
                    $value = $reader.GetValue($i)
                    $row[$columns[$i]] = $(if ($value -is [DBNull]) { $null } else { $value })
                }
                $rows += $row
            }
            $entry.status='EXECUTED'; $entry.columns=$columns; $entry.rows=$rows
        } catch { $entry.status='FAILED'; $entry.error=$_.Exception.Message }
        finally { if ($reader) { $reader.Close() } }
        $result.queries += $entry
    }
} finally {
    $connection.Close()
    $result | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $OutputFile -Encoding UTF8
}
if (@($result.queries | Where-Object status -ne 'EXECUTED').Count -gt 0) { exit 1 }
