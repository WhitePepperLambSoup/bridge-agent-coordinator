"""Bridge 工具函数模块。"""
import os

def _atomic_write(filepath, content):
    """原子写入：先写临时文件，成功后再 rename。防止中途崩溃留下半成品。"""
    import tempfile
    dirname = os.path.dirname(filepath)
    fd, tmp = tempfile.mkstemp(dir=dirname, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, filepath)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

def _sanitize_agent_name(name):
    """消毒 Agent 名称：只保留字母数字连字符下划线，防路径逃逸"""
    import re
    safe = re.sub(r'[^a-zA-Z0-9_\-]', '-', name)
    safe = safe.strip('-').strip('_')
    if not safe:
        safe = "agent"
    # 防目录穿越：去掉 .. 和路径分隔符
    safe = safe.replace('..', '-').replace('/', '-').replace('\\', '-')
    return safe[:64]  # 限制长度

def _check_git_repo(target_dir):
    """检查目标目录是否为有效的 Git 仓库（支持 worktree）"""
    import subprocess
    git_path = os.path.join(target_dir, ".git")
    # Worktree 的 .git 是文件（内含 gitdir: 引用），普通仓库是目录
    if not (os.path.isdir(git_path) or os.path.isfile(git_path)):
        return False, f"目录 {target_dir} 不是 Git 仓库。\n请先运行: cd {target_dir} && git init"
    try:
        result = subprocess.run(["git", "-C", target_dir, "rev-parse", "--is-inside-work-tree"],
                              capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return False, "Git 工作树不可用。"
        # 额外检查：获取 remote 和当前分支
        remote = subprocess.run(["git", "-C", target_dir, "remote"], capture_output=True, text=True, timeout=5)
        branch = subprocess.run(["git", "-C", target_dir, "branch", "--show-current"], capture_output=True, text=True, timeout=5)
        if remote.stdout.strip():
            return True, f"Git 仓库就绪 (remote: {remote.stdout.strip().split()[0]}, branch: {branch.stdout.strip()})"
        return True, "Git 仓库就绪（无 remote，仅本地）。"
    except FileNotFoundError:
        return False, "未找到 git 命令。请安装 Git。"
    except Exception as e:
        return False, f"Git 检查失败: {e}"
