# deepseek-create-key.ps1
# DeepSeek Platform API Key 创建脚本 (纯 PowerShell 原生实现)

param(
    [Parameter(Mandatory)]
    [string]$Token,

    [Parameter(Mandatory)]
    [string]$KeyName,

    [string]$BaseUrl = "https://platform.deepseek.com"
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "Stop"

# ── 构造请求体 ──────────────────────────────────────────────
$body = @{
    action       = "create"
    name         = $KeyName
    redacted_key = $null
    created_at   = $null
    tracking_id  = $null
} | ConvertTo-Json -Compress

# ── 请求头 ──────────────────────────────────────────────────
$headers = @{
    "Authorization"     = "Bearer $Token"
    "Content-Type"      = "application/json"
    "User-Agent"        = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0"
    "Origin"            = $BaseUrl
    "Referer"           = "$BaseUrl/api_keys"
    "x-client-bundle-id" = "com.deepseek.chat"
    "x-client-locale"   = "zh_CN"
    "x-client-platform" = "web"
    "x-client-version"  = "1.0.0"
}

# ── 发送请求 ────────────────────────────────────────────────
$uri = "$BaseUrl/api/v0/users/edit_api_keys"

Write-Host "正在创建 API Key「$KeyName」..." -ForegroundColor Cyan

try {
    $response = Invoke-RestMethod -Uri $uri `
        -Method Post `
        -Headers $headers `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) `
        -ErrorAction Stop

    # ── 解析并输出结果 ──────────────────────────────────────
    if ($response.code -eq 0 -and $response.data.biz_code -eq 0) {
        $keyData = $response.data.biz_data.api_key

        Write-Host ""
        Write-Host "API Key 创建成功！" -ForegroundColor Green
        Write-Host "┌─────────────────────────────────────────────────────"
        Write-Host "│ Name:        $($keyData.name)"
        Write-Host "│ Key:         $($keyData.sensitive_id)" -ForegroundColor Yellow
        Write-Host "│ Tracking ID: $($keyData.tracking_id)"
        Write-Host "│ Created At:  $([DateTimeOffset]::FromUnixTimeSeconds($keyData.created_at).ToLocalTime().ToString('yyyy-MM-dd HH:mm:ss'))"
        Write-Host "└─────────────────────────────────────────────────────"
        Write-Host ""
        Write-Host "请立即保存密钥，此密钥仅显示一次！" -ForegroundColor Red

        # 返回对象方便管道使用
        return [PSCustomObject]@{
            Name        = $keyData.name
            ApiKey      = $keyData.sensitive_id
            TrackingId  = $keyData.tracking_id
            CreatedAt   = [DateTimeOffset]::FromUnixTimeSeconds($keyData.created_at).UtcDateTime
        }
    } else {
        Write-Host "API 返回错误：" -ForegroundColor Red
        Write-Host "   code=$($response.code) msg=$($response.msg)"
        Write-Host "   biz_code=$($response.data.biz_code) biz_msg=$($response.data.biz_msg)"
        exit 1
    }
} catch {
    Write-Host "请求失败：" -ForegroundColor Red
    Write-Host "   $($_.Exception.Message)" -ForegroundColor Red

    # 兼容 Windows PowerShell 5.1 的错误详情读取
    if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
        Write-Host "响应详情：$($_.ErrorDetails.Message)" -ForegroundColor DarkYellow
    }
    exit 1
}