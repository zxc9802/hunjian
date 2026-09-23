param([string]$Workspace = 'D:\海南康养素材混剪', [int]$Port = 8767)
$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $Workspace '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw '请先在工作区创建 Python 3.12 .venv 并安装 requirements.txt 中的依赖。'
}
foreach ($name in @('OPENLUX_API_KEY', 'RERANK_API_KEY')) {
    if (-not [Environment]::GetEnvironmentVariable($name, 'Process')) {
        $secure = Read-Host "输入 $name（仅本进程使用）" -AsSecureString
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try { [Environment]::SetEnvironmentVariable($name, [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr), 'Process') }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
    }
}
$env:PYTHONIOENCODING = 'utf-8'
Push-Location -LiteralPath $Workspace
try { & $pythonPath (Join-Path $PSScriptRoot 'cli.py') ui --port $Port }
finally { Pop-Location }
