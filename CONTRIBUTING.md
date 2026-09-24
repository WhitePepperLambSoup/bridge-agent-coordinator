# Contributing / 参与贡献

## English

1. Open an issue before making a large behavioral or schema change.
2. Create a focused branch and keep unrelated changes out of the pull request.
3. Preserve fail-closed safety behavior and attempt isolation.
4. Add focused tests for every behavior change.
5. Run the verification commands below before opening a pull request.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m compileall -q bridgelib tests
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

Developer comments and docstrings must be English. User-visible Chinese and English text
must remain in the appropriate localization or generated-document path.

## 中文

1. 较大的行为或数据库 Schema 变更请先创建 Issue 讨论。
2. 使用独立分支，Pull Request 不要混入无关修改。
3. 不得削弱 fail-closed、安全确认和 attempt 隔离规则。
4. 每项行为变更都应增加针对性测试。
5. 提交 Pull Request 前运行上述完整验证命令。

开发者注释和 docstring 统一使用英语。用户可见的中文和英文内容应保留在对应的本地化
或文档生成路径中。
