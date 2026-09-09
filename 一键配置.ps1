# Copyright (c) 2026 dan-cun · 许可协议见项目根目录 LICENSE
# =====================================================================
#  乐跑打卡助手 —— 一键环境配置
#  适用: 乐跑打卡助手部署目录(双击 EXE 或命令行 cli.py 前执行一次)
#  幂等: 可重复运行; 已有健康环境则自动跳过安装
#  用法:
#    powershell -ExecutionPolicy Bypass -File .\一键配置.ps1
#    powershell -ExecutionPolicy Bypass -File .\一键配置.ps1 -RegisterPath   # 额外把 mitmweb 写进用户环境变量
#    powershell -ExecutionPolicy Bypass -File .\一键配置.ps1 -ForceReinstall # 强制重装 mitmproxy
# =====================================================================
param(
    [switch]$InstallPython,      # 缺 Python 时用 winget 安装 Python 3.11(仅命令行模式需要)
    [switch]$RegisterPath,       # 把定位到的 mitmweb 写入用户环境变量 LEPAO_MITM_EXE(可选)
    [switch]$InstallWechat,      # 缺微信时用 winget 安装(可选)
    [switch]$ForceReinstall      # 强制重装 mitmproxy(默认有健康实例则跳过)
)
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Say($tag, $msg) { Write-Host ("[{0}] {1}" -f $tag, $msg) }
function Probe-Mitm($p) {
    if (-not $p -or -not (Test-Path $p)) { return $false }
    try {
        $r = & $p --version 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0 -and $r -match "mitmproxy") { return $true }
    } catch { }
    return $false
}

Say "步骤" "====== 一键环境配置: $ScriptDir ======"
Say "许可" "乐跑打卡助手 · Copyright (c) 2026 dan-cun · 乐跑研究协议 1.0(LRL-1.0)"
Say "许可" "仅供学习 · 不可牟利 · 再发布须署名引用 https://github.com/dan-cun/lepao3 且声明不得删除"

# ---------------- 1) Python(命令行模式必需; EXE 模式仅用于装 mitmproxy) ----------------
Say "1/7" "检查 Python ..."
$pyOk = $false
try { $v = py -3 --version 2>&1 | Out-String; if ($v -match "3\.(1[0-9]|[2-9][0-9])") { $pyOk = $true; Say "OK" ("Python " + $v.Trim()) } } catch { }
if (-not $pyOk) {
    Say "提示" "未找到 Python3(命令行 cli.py 需要; 仅用 EXE 可跳过后续依赖但 mitmweb 仍需)"
    if ($InstallPython) {
        Say "安装" "winget 安装 Python 3.11 ..."
        winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements
        $pyOk = $true
    }
}

# ---------------- 2) mitmweb(核心依赖: 抓流量必需) ----------------
Say "2/7" "定位/安装 mitmweb(流量抓取核心) ..."
$candidates = @()
if ($env:LEPAO_MITM_EXE) { $candidates += $env:LEPAO_MITM_EXE }
$candidates += (Join-Path $ScriptDir "tools\mitmweb.exe")   # 目录内自带(可选)
$candidates += @(Get-Command mitmweb -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
foreach ($base in @("$env:LOCALAPPDATA\Programs\Python")) {
    if (Test-Path $base) {
        Get-ChildItem "$base\*\Scripts\mitmweb*.exe" -ErrorAction SilentlyContinue | ForEach-Object { $candidates += $_.FullName }
    }
}
$candidates = $candidates | Select-Object -Unique
$mitm = ""
foreach ($p in $candidates) {
    if (Probe-Mitm $p) { $mitm = $p; break }
}
if ($mitm) {
    Say "OK" ("发现健康 mitmweb: $mitm")
} else {
    if ($pyOk -and -not $ForceReinstall) {
        Say "缺失" "未找到 mitmweb → 尝试 pip 安装(镜像可加 -i https://mirrors.aliyun.com/pypi/simple/) ..."
    }
    if ($pyOk) {
        py -3 -m pip install --upgrade pip 2>&1 | Out-Null
        py -3 -m pip install mitmproxy 2>&1 | Out-Null
        py -3 -m pip install "bcrypt<4.1" 2>&1 | Out-Null   # 防 passlib 兼容问题导致 mitmweb 启动即崩
        foreach ($p in $candidates) { if (Probe-Mitm $p) { $mitm = $p; break } }
        if (-not $mitm) {
            $sp = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\*\Scripts\mitmweb*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($sp -and (Probe-Mitm $sp.FullName)) { $mitm = $sp.FullName }
        }
    }
    if ($mitm) { Say "OK" ("mitmweb 就绪: $mitm") }
    else { Say "失败" "mitmweb 不可用! EXE/CLI 无法抓取登录流量。请装 mitmproxy 后重跑本脚本" }
}
if ($RegisterPath -and $mitm) {
    [Environment]::SetEnvironmentVariable("LEPAO_MITM_EXE", $mitm, "User")
    $env:LEPAO_MITM_EXE = $mitm
    Say "OK" ("已写用户环境变量 LEPAO_MITM_EXE = " + $mitm + " (新开终端生效)")
}

# ---------------- 3) 依赖包(命令行模式: 加解密/br 解码) ----------------
Say "3/7" "Python 依赖(pycryptodome / brotli) ..."
if ($pyOk) {
    py -3 -m pip install pycryptodome brotli | Out-Null
    Say "OK" "pycryptodome + brotli 就绪"
} else { Say "跳过" "无 Python(EXE 模式自带依赖)" }

# ---------------- 4) Clash 出口(可选: 不在线自动直连模式) ----------------
Say "4/7" "检查 Clash 出口(127.0.0.1:7897) ..."
$clashUp = $false
try { $c = New-Object Net.Sockets.TcpClient; $c.Connect("127.0.0.1", 7897); $clashUp = $true; $c.Close() } catch { }
if ($clashUp) { Say "OK" "Clash 在听 → 走 系统代理→mitm→Clash 同态链" }
else { Say "提示" "Clash 未监听 → 程序自动【直连模式】(登录与业务同走本机 mitm 出口; 若小程序当时经代理则需开 Clash 保持一致)" }

# ---------------- 5) 微信(打卡操作必需) ----------------
Say "5/7" "检查电脑版微信 ..."
$wx = ""
foreach ($p in @("$env:ProgramFiles\Tencent\Weixin\Weixin.exe", "${env:ProgramFiles(x86)}\Tencent\Weixin\Weixin.exe", "$env:LOCALAPPDATA\Tencent\Weixin\Weixin.exe", "${env:ProgramFiles(x86)}\Tencent\WeChat\WeChat.exe", "$env:ProgramFiles\Tencent\WeChat\WeChat.exe")) {
    if ($p -and (Test-Path $p)) { $wx = $p; break }
}
if ($wx) { Say "OK" ("微信已安装: $wx") }
else {
    Say "缺失" "未找到电脑版微信(4.x Weixin / 3.x WeChat) → 打卡需要它登录小程序"
    if ($InstallWechat) {
        Say "安装" "winget 安装微信 ..."; winget install -e --id Tencent.WeChat --accept-package-agreements --accept-source-agreements
    }
}

# ---------------- 6) 运行配置检查 ----------------
Say "6/7" "检查运行配置 config.json ..."
$cfg = Join-Path $ScriptDir "config.json"
if (Test-Path $cfg) {
    try {
        $j = Get-Content $cfg -Raw -Encoding UTF8 | ConvertFrom-Json
        $n = @($j.accounts.PSObject.Properties).Count
        Say "OK" ("config.json 存在, 已登记成员 " + $n + " 个")
        if ($n -eq 0) { Say "提示" "成员表为空 → 打开程序点『成员管理→新增成员』登记(学号即抓号白名单)" }
    } catch { Say "提示" "config.json 无法解析, 可删除后由程序自动重建" }
} else {
    Say "提示" "无 config.json → 首次运行程序会自动创建(或复制 config.sample.json)"
    if (Test-Path (Join-Path $ScriptDir "config.sample.json")) {
        Copy-Item (Join-Path $ScriptDir "config.sample.json") $cfg -Force
        Say "OK" "已从 config.sample.json 初始化"
    }
}

# ---------------- 7) 汇总 ----------------
Say "7/7" "====== 配置汇总 ======"
$rows = @(
    "Python3        : " + $(if ($pyOk) { "OK" } else { "无需(EXE模式)/缺失" }),
    "mitmweb        : " + $(if ($mitm) { "OK -> $mitm" } else { "不可用(必须先装)" }),
    "Clash(7897)    : " + $(if ($clashUp) { "OK" } else { "未开(直连模式可用)" }),
    "微信           : " + $(if ($wx) { "OK -> $wx" } else { "缺失(必须装)" })
)
$rows | ForEach-Object { Say "状态" $_ }
Write-Host ""
Say "完成" "自检: 双击 乐跑打卡助手.exe(右上'环境自检'); 或 .\乐跑打卡助手.exe --selfcheck"
Say "完成" "每日打卡: 启动程序 → (先退出微信登录) → 扫码 → 打开小程序「数体智慧体育」一次"
if (-not $mitm) { exit 1 } else { exit 0 }
