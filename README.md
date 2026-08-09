# WeLearn Studio

WeLearn Studio 是面向 Windows 的多账号课程任务工作台，用于集中管理账号、课程、单元、小课选择、任务参数和运行状态。项目采用独立规格与全新代码实现。

> 当前版本：`0.1.0 Alpha`。不同账号、课程和平台响应之间仍可能存在兼容性差异。远程请求被接受不代表平台最终统计一定发生变化。

## 下载与启动

普通用户请从 [Releases 页面](https://github.com/ww2186874-cyber/Welearn_Studio/releases/latest)下载最新版 Windows 便携包：

1. 下载 `WeLearn-Studio-windows-x64.zip`。
2. 将压缩包完整解压到普通文件夹。
3. 双击 `WeLearn-Studio.exe` 启动。

便携版已经包含 Python 和运行依赖，不需要安装 Python 或 PowerShell 7。程序目前没有代码签名，Windows 首次运行时可能显示“未知发布者”提示。

GitHub 的“Code → Download ZIP”和 Release 中自动生成的 `Source code` 文件都是源码，不能直接双击运行。仓库中的 `start-studio.cmd` 也只是源码环境启动器，不是安装器。

GitHub 侧栏中的 **Releases** 用于发布普通用户可下载的正式版本；**Packages** 是供其他工程依赖的软件包注册表，本桌面项目目前不使用，显示为空是正常状态。两者的详细区别参见[发布流程](docs/RELEASE.md)。

## 主要功能

- 三栏深色工作台，支持 `80%` 至 `200%` 界面缩放、`Ctrl+滚轮` 和 `Ctrl+0`。
- 多账号导入，以及相互独立的会话、配置、日志和任务状态。
- 按账号与课程保存课程、单元、小课选择、任务参数和完整课程预设。
- 刷作业与刷时长共用单元和小课选择。
- 总时长按全部已选可运行小课以整分钟平均分配，不能整除的余数舍弃。
- 支持 `1` 至 `100` 个并发、分批执行、协作式停止、任务进度和秒级倒计时。
- 明确区分请求已接受、已拒绝、结果未知和已取消，避免把接口响应误认为平台最终结果。
- 高 DPI 深色界面，以及可搜索的小课选择和课程预设窗口。

## 运行环境

从源码运行需要：

- Windows 10 或 Windows 11（64 位）；
- Python 3.12 或更高版本；
- PowerShell 7，命令名称为 `pwsh`；
- 可正常访问对应平台的网络环境。

本项目的源码安装、验证和打包命令统一以 PowerShell 7 为准。便携版用户不需要安装 Python 或 PowerShell 7。

## 源码安装与启动

在项目目录中打开 PowerShell 7，然后执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\start-studio.cmd
```

首次安装完成后，可以直接双击 `start-studio.cmd` 启动，不需要每次手动打开 PowerShell。

如果双击后提示环境未安装，说明当前目录缺少 `.venv`，需要重新执行上述安装命令。移动项目目录后，也建议重新创建虚拟环境。

## 基本使用

1. 在左侧添加账号，或导入本机账号文件。
2. 选中账号后点击课程栏的“刷新”，完成登录并读取课程。
3. 选择课程、单元和需要运行的小课。
4. 设置任务参数并检查右侧预计时间。
5. 点击“开始”，通过账号状态、进度条、倒计时和运行日志查看进度。

程序会保存非敏感的界面与课程配置，但不会保存密码。重新启动后仍需使用当前进程中的账号凭据完成登录。

## 账号文件

支持 CSV 和 TXT。CSV 示例：

```csv
username,password,nickname
example@example.test,replace-me,示例账号
```

CSV 可以不带表头，字段顺序为“账号、密码、可选昵称”。TXT 支持空格、制表符、逗号或分号分隔。

账号文件包含明文密码，只应保存在受控的本机目录。不要上传到网盘、提交到 Git，或附加到公开 Issue。

## 本地数据与隐私

非敏感工作区配置默认保存在 Windows 应用配置目录下的 `workspace.json`，内容包括界面缩放、最近账号文件路径、课程选择、单元与小课选择和课程预设。

程序不会把密码、Cookie 或授权令牌写入工作区配置。关闭程序后，当前进程内的登录凭据和会话随之失效。

详细要求参见[安全说明](SECURITY.md)。

## 开发与验证

开发者需要安装额外依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
pwsh -NoProfile -File .\scripts\verify.ps1
```

验证脚本会依次运行静态检查、格式检查、离线测试、编译检查和 Git 空白检查。自动化测试只能使用合成账号与合成协议响应，不得连接真实服务。

## 工程文档

- [参与贡献](CONTRIBUTING.md)
- [安全说明](SECURITY.md)
- [产品规格](docs/PRODUCT_SPEC.md)
- [架构说明](docs/ARCHITECTURE.md)
- [远程协议规格](docs/PROTOCOL_SPEC.md)
- [独立实现记录](docs/CLEAN_ROOM.md)
- [开发指南](docs/DEVELOPMENT.md)
- [发布流程](docs/RELEASE.md)

## 开源许可证

WeLearn Studio 自有代码采用 [MIT License](LICENSE) 开源。你可以使用、复制、修改和分发本项目，也可以用于商业项目，但必须在副本或主要衍生内容中保留原版权声明和 MIT 许可证文本。

MIT 许可证不为软件提供任何担保，也不改变 PySide6、requests、Python 等第三方组件各自的许可证。第三方依赖信息参见[第三方依赖说明](THIRD_PARTY_NOTICES.md)。
