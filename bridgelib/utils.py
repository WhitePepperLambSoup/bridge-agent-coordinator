"""Bridge utility functions."""
import os

def _atomic_write(filepath, content):
    """Write atomically through a temporary file to avoid partial output on failure."""
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
    """Sanitize an agent name to alphanumerics, hyphens, and underscores."""
    import re
    safe = re.sub(r'[^a-zA-Z0-9_\-]', '-', name)
    safe = safe.strip('-').strip('_')
    if not safe:
        safe = "agent"
    # Prevent directory traversal by removing .. and path separators.
    safe = safe.replace('..', '-').replace('/', '-').replace('\\', '-')
    return safe[:64]  # Limit the length.

def _check_git_repo(target_dir):
    """Check whether the target is a valid Git repository, including a worktree."""
    import subprocess
    git_path = os.path.join(target_dir, ".git")
    # A worktree's .git is a file containing a gitdir reference; a regular repo uses a directory.
    if not (os.path.isdir(git_path) or os.path.isfile(git_path)):
        return False, f"目录 {target_dir} 不是 Git 仓库。\n请先运行: cd {target_dir} && git init"
    try:
        result = subprocess.run(["git", "-C", target_dir, "rev-parse", "--is-inside-work-tree"],
                              capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return False, "Git 工作树不可用。"
        # Also retrieve the remote and current branch.
        remote = subprocess.run(["git", "-C", target_dir, "remote"], capture_output=True, text=True, timeout=5)
        branch = subprocess.run(["git", "-C", target_dir, "branch", "--show-current"], capture_output=True, text=True, timeout=5)
        if remote.stdout.strip():
            return True, f"Git 仓库就绪 (remote: {remote.stdout.strip().split()[0]}, branch: {branch.stdout.strip()})"
        return True, "Git 仓库就绪（无 remote，仅本地）。"
    except FileNotFoundError:
        return False, "未找到 git 命令。请安装 Git。"
    except Exception as e:
        return False, f"Git 检查失败: {e}"
