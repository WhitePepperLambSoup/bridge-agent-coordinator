# GitHub Upload Guide / GitHub 上传指南

## 中文

### 1. 准备账号和提交身份

注册并登录 [GitHub](https://github.com/)。在 PowerShell 中确认 Git 可用：

```powershell
git --version
```

首次使用 Git 时设置提交者名称和邮箱：

```powershell
git config --global user.name "你的 GitHub 用户名"
git config --global user.email "你的 GitHub 邮箱"
```

如果不希望公开真实邮箱，可在 GitHub 的 **Settings → Emails** 中启用邮箱隐私，
然后使用 GitHub 提供的 `数字+用户名@users.noreply.github.com` 地址。

### 2. 检查干净发布目录

本项目已经在以下目录创建了全新的 Git 仓库，不包含原项目 Git 历史或本地 Agent 记录：

```powershell
cd "<path-to-bridge>\to github"
git status
```

应看到源码、文档和测试为未跟踪文件，不应看到 `.reasonix`、`.venv`、数据库、缓存、
`build` 或 `dist`。

### 3. 创建第一次本地提交

```powershell
git add .
git status
git commit -m "Initial public release"
```

`git status` 用于在提交前再次确认文件列表。如果出现不认识的文件，先不要提交。

### 4. 在 GitHub 网页创建空仓库

1. 打开 <https://github.com/new>。
2. Repository name 填写 `bridge-agent-coordinator`。
3. Description 可填写 `Local coordinator for manually operated AI coding agents`。
4. 根据需要选择 **Public** 或 **Private**。
5. 不要勾选 README、`.gitignore` 或 License；本地已经包含这些文件。
6. 点击 **Create repository**。

### 5. 添加远程地址并上传

将下面的 `YOUR_NAME` 替换为你的 GitHub 用户名：

```powershell
git remote add origin https://github.com/YOUR_NAME/bridge-agent-coordinator.git
git remote -v
git push -u origin main
```

GitHub 不接受账号密码作为 Git 密码。Windows 通常会通过 Git Credential Manager 打开
浏览器登录；按浏览器提示授权即可。如果环境要求 Personal Access Token，请在 GitHub
Settings 中创建 Token，并将 Token 当作密码使用。不要把 Token 写入源码或命令脚本。

### 6. 补充项目 URL

知道最终仓库地址后，可在 `pyproject.toml` 的 `[project]` 后加入：

```toml
[project.urls]
Homepage = "https://github.com/YOUR_NAME/bridge-agent-coordinator"
Repository = "https://github.com/YOUR_NAME/bridge-agent-coordinator"
Issues = "https://github.com/YOUR_NAME/bridge-agent-coordinator/issues"
Documentation = "https://github.com/YOUR_NAME/bridge-agent-coordinator/tree/main/docs"
```

然后提交并推送：

```powershell
git add pyproject.toml
git commit -m "docs: add project URLs"
git push
```

### 7. 检查 GitHub Actions

上传后打开仓库的 **Actions** 页面。`Test and build` 工作流应在 Python 3.11 和 3.13
上运行测试、构建 wheel/sdist、检查元数据并安装 wheel。所有任务应显示绿色。

如果工作流未运行，在仓库 **Settings → Actions → General** 中确认 Actions 已启用。

### 8. 创建 v0.1.0 Release

确认 Actions 通过后创建版本标签：

```powershell
git tag -a v0.1.0 -m "Bridge 0.1.0"
git push origin v0.1.0
```

在 GitHub 仓库页面选择 **Releases → Draft a new release**，选择 `v0.1.0`，标题填写
`Bridge 0.1.0`，发布说明可参考 `CHANGELOG.md`。Actions 生成的 distributions artifact
可以下载后作为 Release 附件上传。

### 9. 推荐的仓库设置

- 在 About 中填写描述和 Topics：`ai-agents`、`git-worktree`、`code-review`、`python`。
- 在 **Settings → Code security** 中启用 Private vulnerability reporting。
- 稳定后为 `main` 启用分支保护，要求 Actions 通过后才能合并。
- 不要使用 `git push --force` 覆盖公开历史。

### 10. 以后更新项目

```powershell
cd "<path-to-bridge>\to github"
git status
git add .
git commit -m "描述本次修改"
git pull --rebase origin main
git push
```

每次提交前检查 `git status`，不要提交 API Key、数据库、`.env`、虚拟环境或聊天记录。

## English

### First Publication

1. Create and sign in to a GitHub account.
2. Configure `git config --global user.name` and `user.email` once.
3. Open `<path-to-bridge>\to github` in PowerShell.
4. Review `git status`, then run `git add .` and `git commit -m "Initial public release"`.
5. Create an empty repository named `bridge-agent-coordinator` at <https://github.com/new>.
   Do not initialize it with a README, `.gitignore`, or license.
6. Add the HTTPS remote and push:

```powershell
git remote add origin https://github.com/YOUR_NAME/bridge-agent-coordinator.git
git push -u origin main
```

Use Git Credential Manager or a Personal Access Token; GitHub account passwords do not
work for Git operations. Never store a token in this repository.

After the push, verify the **Test and build** workflow on the Actions page. Once it passes,
create and push the `v0.1.0` annotated tag and draft a GitHub Release using `CHANGELOG.md`.

For later updates, review `git status`, commit focused changes, run
`git pull --rebase origin main`, and push. Never force-push the public default branch.
