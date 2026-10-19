# Stop the StackChan MCP bridge: find python processes running mcp_pipe.py and kill their process trees.
# NOTE: keep this file ASCII-only (Windows PowerShell 5.1 reads non-BOM UTF-8 as ANSI and may misparse).
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*mcp_pipe.py*' }
if (-not $procs) {
    Write-Host "[i] No running bridge process found (it may already be stopped)."
    exit 0
}
foreach ($p in $procs) {
    Write-Host "[>] Killing PID $($p.ProcessId) ..."
    & taskkill /F /T /PID $p.ProcessId 2>&1 | Out-Null
}
Write-Host "[OK] Bridge stopped. Normal voice chat on the device is NOT affected."
