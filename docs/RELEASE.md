# 发布流程

## 发布前检查

1. 确认 `pyproject.toml` 与 `src/welearn_studio/__init__.py` 中的版本号一致。
2. 将 `CHANGELOG.md` 中待发布的内容整理到对应版本标题下。
3. 在 Windows 和 PowerShell 7 中运行完整验证。
4. 检查仓库中不存在凭据、账号文件、Cookie、本机绝对路径和个人截图。
5. 确认 README 中记录的远程协议限制仍然准确。

## 本地构建

```powershell
pwsh -NoProfile -File .\scripts\build-release.ps1
```

构建脚本会先验证仓库，再使用隔离设置启动一次打包后的应用，并生成：

- `dist/python`：Python wheel 和源码分发包；
- `dist/releases/<版本>/app/WeLearn-Studio`：带版本号的 Windows 应用目录；
- `dist/WeLearn-Studio-<版本>-windows-x64.zip`：带版本号的便携压缩包；
- `dist/WeLearn-Studio-windows-x64.zip`：指向当前版本的便携压缩包。

按版本区分应用目录，可以避免仍在运行的旧版本阻塞新版本构建。

便携版目前没有代码签名。获得代码签名证书并接入签名步骤前，Windows 可能显示“未知发布者”警告。

## GitHub Release

推送与程序版本一致的标签，例如 `v0.1.0`：

```powershell
git tag v0.1.0
git push origin v0.1.0
```

标签会触发发布工作流。工作流运行完整验证、生成分发文件、上传构建产物，并创建带自动发行说明的 GitHub Release。

也可以从 GitHub Actions 手动运行发布工作流来测试打包。手动运行只上传临时 Actions 构建产物，不创建正式 Release。

## Releases、Packages 与 Actions 构建产物

- **Releases**：面向软件使用者的正式版本页面。便携版 ZIP、安装包、wheel、源码包和版本说明都应放在这里。普通用户应从 Releases 下载软件。
- **Packages**：GitHub 的软件包注册表，用于容器镜像、npm、NuGet、Maven 等可被其他工程依赖的包。本项目目前不向 GitHub Packages 发布内容，保持为空是正常状态。
- **Actions 构建产物**：自动化工作流生成的临时文件，主要供开发者测试和下载，具有保存期限，不应替代正式 Release。

本项目发布桌面便携版时使用 Releases，不需要为了填充仓库侧栏而额外使用 Packages。
