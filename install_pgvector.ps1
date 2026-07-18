$src = "$env:TEMP\pgvector-pg18"

# Search for PostgreSQL installations dynamically
$pgVersions = @("17", "16", "15", "14")
$pgRoot = ""
foreach ($v in $pgVersions) {
    $path = "C:\Program Files\PostgreSQL\$v"
    if (Test-Path $path) {
        $pgRoot = $path
        break
    }
}

if (-not $pgRoot) {
    # Default fallback to 16 if none found
    $pgRoot = "C:\Program Files\PostgreSQL\16"
}

Write-Host "Installing pgvector for PostgreSQL at: $pgRoot"

Copy-Item "$src\lib\vector.dll" "$pgRoot\lib\vector.dll" -Force
Copy-Item "$src\share\extension\*" "$pgRoot\share\extension\" -Force

Write-Host "pgvector files installed successfully to $pgRoot"
