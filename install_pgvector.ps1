$src = "$env:TEMP\pgvector-pg18"
$pgRoot = "C:\Program Files\PostgreSQL\18"

Copy-Item "$src\lib\vector.dll" "$pgRoot\lib\vector.dll" -Force
Copy-Item "$src\share\extension\*" "$pgRoot\share\extension\" -Force

Write-Host "pgvector files installed successfully to $pgRoot"
