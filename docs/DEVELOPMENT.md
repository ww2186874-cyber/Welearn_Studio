# 开发指南

## 环境要求

- Windows 10 或 Windows 11（64 位）；
- Python 3.12 或更高版本；
- PowerShell 7（`pwsh`）；
- Git。

项目的源码安装、验证和打包命令统一使用 PowerShell 7。

## 安装开发环境

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## 完整验证

```powershell
pwsh -NoProfile -File .\scripts\verify.ps1
```

该脚本依次执行 Ruff 静态检查、格式检查、离线单元测试、编译检查和 Git 空白检查。

## 隔离启动

进行界面验收时，可以使用隔离设置启动，避免恢复正常账号文件和工作区状态：

```powershell
$env:WELEARN_STUDIO_SETTINGS_PATH = "$env:TEMP\welearn-studio-isolated.json"
$env:WELEARN_STUDIO_NO_RESTORE = "1"
.\.venv\Scripts\python.exe -m welearn_studio.app
```

测试必须使用合成账号和合成协议响应。任何会连接真实平台的自动化测试都属于缺陷。

## 修改边界

- 先在领域层添加行为，再接入 Qt 界面。
- 远程 JSON 和表单字段只能存在于适配器边界。
- 协议观察应与实现变化分别记录。
- 不得复制其他实现的源码片段作为开发起点。
- 面向用户的请求结果文字必须与明确的结果模型一致。

## 提交规范

提交应保持范围清晰，并说明最终行为。不得提交虚拟环境、账号导出文件、日志、包含个人信息的截图、生成的构建产物或本地设置。

版本准备和标签规则参见[发布流程](RELEASE.md)。
