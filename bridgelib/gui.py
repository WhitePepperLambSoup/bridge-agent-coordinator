"""Bridge GUI module: BridgeApp and its entry point.

Desktop coordination console integrating the BridgeCoordinator core:
- Project connection and Git preflight checks
- Agent role configuration (planning/review, execution, optional third executor)
- One valid next action for each task state
- Automatic management of leases, attempts, worktrees, receipts, reviews, and merge IDs
- Optional Markdown document generation and LLM assistance
"""
import os
import sys
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from pathlib import Path
from datetime import datetime
import threading
import tempfile
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

from bridgelib.i18n import T, set_lang, LANG
from bridgelib.templates import TEMPLATES
from bridgelib.utils import _atomic_write, _sanitize_agent_name, _check_git_repo
from bridgelib.generators import (
    _stage_display, generate_agents_md, generate_collab_md,
    generate_tasks_md, generate_review_template, generate_fix_template,
    generate_acceptance_md, generate_readme_md, generate_agent_status_md,
    generate_board_md, generate_git_worktree_guide, generate_parallel_struct,
    generate_loop_budget_md, generate_verify_template, generate_spec_claim_template
)
from bridgelib.llm import call_llm, llm_enhance_description
from bridgelib.models import get_models_by_tier, MODEL_REGISTRY, fetch_latest_models
from bridgelib.database import Database, init_database
from bridgelib.coordinator import BridgeCoordinator, CoordinatorError
from bridgelib.state_machine import TaskState, ProgressionPolicy
from bridgelib.review import ReviewPackage, ReviewVerdict
from bridgelib.safety import ConfirmationMode
from bridgelib.protocol import generate_task_package, parse_receipt
from bridgelib.git_adapter import GitRepositoryAdapter


STATE_LABELS = {
    "draft": "待准备",
    "planning": "规划中",
    "ready": "可分配",
    "assigned": "待启动",
    "in_progress": "Agent 执行中",
    "submitted": "待验证",
    "validating": "待审查",
    "approved": "可合并",
    "merge_queued": "合并队列",
    "merging": "合并中",
    "done": "已完成",
    "blocked": "已阻塞",
    "revision_required": "需要修改",
    "escalated": "已升级",
    "conflict": "存在冲突",
    "stale": "已过期",
    "cancelled": "已取消",
}

WORKFLOW_STAGE = {
    "draft": 0, "planning": 0, "ready": 1, "assigned": 1,
    "in_progress": 2, "submitted": 3, "validating": 4,
    "approved": 5, "merge_queued": 5, "merging": 5, "done": 6,
}


@dataclass(frozen=True)
class NextAction:
    key: str
    label: str
    description: str
    enabled: bool = True


def get_next_task_action(state: str, has_pending_review: bool = False) -> NextAction:
    """Return the single user-facing action for a task state."""
    actions = {
        "draft": NextAction("prepare", "准备任务", "检查范围与验收标准，并将任务设为可分配"),
        "planning": NextAction("prepare", "完成准备", "确认任务信息后进入可分配状态"),
        "ready": NextAction("assign", "分配并开始", "选择执行者和审查者，创建隔离工作区"),
        "assigned": NextAction("start", "创建工作区", "获取租约并生成 Agent 启动任务包"),
        "in_progress": NextAction("submit", "检测 Agent 提交", "从任务包读取回执和提交，不需要填写 commit"),
        "submitted": NextAction("validate", "运行验证", "在任务 worktree 中执行检查并生成审查请求"),
        "validating": NextAction(
            "review", "记录审查结果" if has_pending_review else "准备审查",
            "记录独立审查者的批准或修改意见",
        ),
        "approved": NextAction("merge", "安全合并", "串行验证、合并并完成清理"),
        "merge_queued": NextAction("merge", "继续合并", "继续队列中尚未完成的安全合并"),
        "merging": NextAction("merge", "完成合并", "完成目标分支更新并关闭任务"),
        "done": NextAction("done", "任务已完成", "代码已合并，任务记录已关闭", False),
        "cancelled": NextAction("done", "任务已取消", "此任务不再接受操作", False),
        "revision_required": NextAction("retry", "开始修改", "关闭旧尝试并为执行者创建新工作区"),
        "blocked": NextAction("retry", "解除阻塞", "确认问题已解决后重新开始执行"),
        "conflict": NextAction("retry", "处理冲突", "保留现场并重新分配修复"),
        "stale": NextAction("retry", "重新开始", "租约已过期，创建新的执行尝试"),
        "escalated": NextAction("retry", "重新分配", "选择更合适的执行者重新尝试"),
    }
    return actions.get(state, NextAction("none", "暂无可用操作", "请查看活动日志中的详细原因", False))


class ScrollablePage(ttk.Frame):
    """A responsive page with a persistent scrollbar and wheel support."""

    def __init__(self, parent, *, padding=18, min_content_height=0):
        super().__init__(parent, style="App.TFrame")
        self.min_content_height = min_content_height
        self.canvas = tk.Canvas(
            self, background="#f4f6f8", highlightthickness=0, borderwidth=0,
        )
        self.scrollbar = ttk.Scrollbar(
            self, orient=tk.VERTICAL, command=self.canvas.yview,
        )
        self.content = ttk.Frame(
            self.canvas, style="App.TFrame", padding=padding,
        )
        self._window_id = self.canvas.create_window(
            (0, 0), window=self.content, anchor=tk.NW,
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.content.bind("<Configure>", self._on_content_configure)

    def _on_canvas_configure(self, event=None):
        width = event.width if event else self.canvas.winfo_width()
        height = event.height if event else self.canvas.winfo_height()
        requested = self.content.winfo_reqheight()
        self.canvas.itemconfigure(
            self._window_id,
            width=max(width, 1),
            height=max(height, requested, self.min_content_height),
        )
        self._update_scrollregion()

    def _on_content_configure(self, _event=None):
        self.after_idle(self._update_scrollregion)

    def _update_scrollregion(self):
        if not self.winfo_exists():
            return
        bbox = self.canvas.bbox("all")
        if bbox:
            self.canvas.configure(scrollregion=bbox)

    def contains_pointer(self) -> bool:
        if not self.winfo_ismapped():
            return False
        x = self.winfo_pointerx()
        y = self.winfo_pointery()
        return (
            self.winfo_rootx() <= x < self.winfo_rootx() + self.winfo_width()
            and self.winfo_rooty() <= y < self.winfo_rooty() + self.winfo_height()
        )

    def can_scroll(self) -> bool:
        first, last = self.canvas.yview()
        return first > 0.0 or last < 1.0

    def scroll_units(self, units: int):
        if self.can_scroll():
            self.canvas.yview_scroll(units, "units")

    def owns_widget(self, widget) -> bool:
        current = widget
        while current is not None:
            if current is self:
                return True
            current = getattr(current, "master", None)
        return False

    def has_nested_scroll_target(self, widget) -> bool:
        current = widget
        while current is not None and current is not self:
            if isinstance(current, (tk.Text, tk.Listbox, ttk.Treeview, ttk.Combobox)):
                return True
            if isinstance(current, tk.Canvas) and current is not self.canvas:
                return True
            current = getattr(current, "master", None)
        return False


# ═══════════════════════════════════════════════════════════════

class BridgeApp:
    def __init__(self, root):
        self.root = root
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.lang = "zh"
        self.root.title(T("window_title", self.lang))
        self.root.geometry("1180x760")
        self.root.minsize(820, 560)
        self.root.configure(background="#f4f6f8")

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self._configure_styles(style)

        # Data
        self.target_dir = tk.StringVar(value="")
        self.mode = tk.StringVar(value="architect-engineer")
        self.project_name = tk.StringVar(value="")
        self.agent_a_name = tk.StringVar(value="GPT")
        self.agent_a_role = tk.StringVar(value="架构师 / 审核员")
        self.agent_a_model = tk.StringVar(value="gpt-5.6-sol")
        self.agent_b_name = tk.StringVar(value="Reasonix")
        self.agent_b_role = tk.StringVar(value="工程师 / 执行者")
        self.agent_b_model = tk.StringVar(value="deepseek-v4-flash")

        # Optional Agent C
        self.agent_c_enabled = tk.BooleanVar(value=False)
        self.agent_c_name = tk.StringVar(value="Agent C")
        self.agent_c_role = tk.StringVar(value="辅助执行者")
        self.agent_c_model = tk.StringVar(value="claude-haiku-4-5")

        # LLM settings
        self.llm_enabled = tk.BooleanVar(value=False)
        self.llm_api_key = tk.StringVar(value="")
        self.llm_api_base = tk.StringVar(value="https://api.openai.com/v1")
        self.llm_model = tk.StringVar(value="gpt-5.6-luna")
        self.llm_user_input = tk.StringVar(value="")

        # Custom pipeline
        self.custom_pipeline = []

        # Coordinator state
        self.coordinator: BridgeCoordinator | None = None
        self.db: Database | None = None
        self.db_path = tk.StringVar(value="")
        self.current_project_id = tk.StringVar(value="")
        self.progression_policy = tk.StringVar(value="hybrid")
        self.safety_mode = tk.StringVar(value="balanced")
        self.agents: list[dict] = []
        self.tasks: list[dict] = []

        # Dynamic agent list for coordinator mode
        self.dynamic_agents: list[dict] = []

        # Simplified task form and current context
        self.new_task_title = tk.StringVar()
        self.new_task_goal = tk.StringVar()
        self.new_task_paths = tk.StringVar(value="src/**\ntests/**")
        self.new_task_acceptance = tk.StringVar(value="功能满足任务目标\n相关测试通过")
        self.new_task_checks = tk.StringVar(value="unit-tests")
        self.owner_choice = tk.StringVar()
        self.reviewer_choice = tk.StringVar()
        self.selected_task_id = ""
        self.last_handoff_path = ""
        self._task_action = NextAction("none", "请选择任务", "从左侧选择一个任务", False)
        self.scroll_pages: list[ScrollablePage] = []
        self._diff_windows: dict[str, tk.Toplevel] = {}
        self._ai_review_windows: dict[str, tk.Toplevel] = {}
        self._conflict_windows: dict[str, tk.Toplevel] = {}
        self._dag_window: tk.Toplevel | None = None

        self._build_ui()
        self.root.bind_all("<MouseWheel>", self._route_mousewheel, add="+")
        self.root.bind_all("<Button-4>", self._route_mousewheel, add="+")
        self.root.bind_all("<Button-5>", self._route_mousewheel, add="+")
        self.root.bind("<F5>", lambda e: self._refresh_coordinator())
        self.root.bind("<Control-r>", lambda e: self._refresh_coordinator())
        self.root.bind("<Control-R>", lambda e: self._refresh_coordinator())
        self.root.bind("<Control-n>", lambda e: self._show_create_task_dialog())
        self.root.bind("<Control-N>", lambda e: self._show_create_task_dialog())
        self.root.bind("<Control-d>", lambda e: self._gui_view_task_diff())
        self.root.bind("<Control-D>", lambda e: self._gui_view_task_diff())

    def close(self):
        """Close watcher, coordinator, database lock, and the Tk window."""
        if self.coordinator is not None:
            try:
                self.coordinator.close()
            except Exception as exc:
                logger.exception("Failed to close Bridge coordinator: %s", exc)
        elif self.db is not None:
            try:
                self.db.release_lock()
                self.db.close()
            except Exception as exc:
                logger.exception("Failed to close Bridge database: %s", exc)
        self.coordinator = None
        self.db = None
        self.root.destroy()

    def _require_coordinator(self) -> BridgeCoordinator:
        """Return the connected coordinator or raise a user-facing error."""
        if self.coordinator is None:
            raise CoordinatorError("请先连接项目")
        return self.coordinator

    def _configure_styles(self, style):
        """Configure a restrained desktop workspace visual system."""
        style.configure(".", font=("Microsoft YaHei UI", 9), background="#f4f6f8")
        style.configure("App.TFrame", background="#f4f6f8")
        style.configure("Header.TFrame", background="#17212b")
        style.configure("Accent.TFrame", background="#1683c4")
        style.configure("Header.TLabel", background="#17212b", foreground="#ffffff")
        style.configure("MutedHeader.TLabel", background="#17212b", foreground="#aeb9c4")
        style.configure("Surface.TFrame", background="#ffffff")
        style.configure("Surface.TLabel", background="#ffffff", foreground="#17212b")
        style.configure("Muted.TLabel", background="#ffffff", foreground="#65717d")
        style.configure("PageMuted.TLabel", background="#f4f6f8", foreground="#65717d")
        style.configure("Title.TLabel", background="#ffffff",
                        font=("Microsoft YaHei UI", 15, "bold"), foreground="#17212b")
        style.configure("PageTitle.TLabel", background="#f4f6f8",
                        font=("Microsoft YaHei UI", 15, "bold"), foreground="#17212b")
        style.configure("Section.TLabel", background="#ffffff",
                        font=("Microsoft YaHei UI", 10, "bold"), foreground="#17212b")
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(18, 10))
        style.map("Primary.TButton", background=[("active", "#1269a8"), ("!disabled", "#0f78bd")],
                  foreground=[("!disabled", "#ffffff")])
        style.configure("Secondary.TButton", padding=(12, 8))
        style.configure("Danger.TButton", foreground="#a32626", padding=(12, 8))
        style.configure("TEntry", padding=(7, 7), fieldbackground="#ffffff")
        style.configure("TCombobox", padding=(6, 5), fieldbackground="#ffffff")
        style.configure("Treeview", rowheight=34, background="#ffffff",
                        fieldbackground="#ffffff", borderwidth=0)
        style.map("Treeview",
                  background=[("selected", "#d9ecf8")],
                  foreground=[("selected", "#123f59")])
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"),
                        padding=8, background="#e9eef2", foreground="#34434f")
        style.map("Treeview.Heading", background=[("active", "#dde6ec")])
        style.configure("Vertical.TScrollbar", arrowsize=13)
        style.configure("TNotebook", background="#f4f6f8", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(20, 10), font=("Microsoft YaHei UI", 9, "bold"))
        style.map("TNotebook.Tab",
                  background=[("selected", "#ffffff"), ("!selected", "#dfe4e8")],
                  foreground=[("selected", "#0f5f91"), ("!selected", "#35414c")])

    def _build_ui(self):
        main_frame = ttk.Frame(self.root, style="App.TFrame")
        main_frame.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(main_frame, style="Header.TFrame", padding=(22, 14))
        header.pack(fill=tk.X)
        ttk.Label(header, text="Bridge", style="Header.TLabel",
                  font=("Microsoft YaHei UI", 17, "bold")).pack(side=tk.LEFT)
        ttk.Label(header, text="本地多 Agent 协调台", style="MutedHeader.TLabel").pack(
            side=tk.LEFT, padx=(12, 0), pady=(5, 0)
        )
        self.connection_label = ttk.Label(
            header, text="未连接项目", style="MutedHeader.TLabel"
        )
        self.connection_label.pack(side=tk.RIGHT, pady=(5, 0))
        ttk.Frame(main_frame, height=3, style="Accent.TFrame").pack(fill=tk.X)

        self.notebook = ttk.Notebook(main_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=12, pady=(10, 6))
        self.notebook.enable_traversal()

        project_tab = self._add_scrollable_tab("项目", padding=18, min_content_height=560)
        self._build_project_tab(project_tab)

        agent_tab = self._add_scrollable_tab("Agent", padding=18, min_content_height=650)
        self._build_agent_tab(agent_tab)

        task_tab = self._add_scrollable_tab("任务", padding=12, min_content_height=650)
        self._build_coordinator_tab(task_tab)

        tools_tab = self._add_scrollable_tab("工具", padding=12, min_content_height=620)
        self._build_tools_tab(tools_tab)

        status_bar = ttk.Frame(main_frame, style="Surface.TFrame", padding=(16, 7))
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        self.status_label = ttk.Label(status_bar, text="请选择一个 Git 项目开始", style="Muted.TLabel")
        self.status_label.pack(side=tk.LEFT)
        ttk.Button(status_bar, text="刷新", style="Secondary.TButton",
                   command=self._refresh_coordinator).pack(side=tk.RIGHT)

        self._on_mode_change()
        self._init_custom_pipeline()

    def _add_scrollable_tab(self, label: str, *, padding: int,
                            min_content_height: int) -> ttk.Frame:
        host = ttk.Frame(self.notebook, style="App.TFrame")
        page = ScrollablePage(
            host, padding=padding, min_content_height=min_content_height,
        )
        page.pack(fill=tk.BOTH, expand=True)
        self.scroll_pages.append(page)
        self.notebook.add(host, text=label)
        return page.content

    def _route_mousewheel(self, event):
        """Route wheel input to the visible page unless a nested widget owns it."""
        for page in self.scroll_pages:
            try:
                if not page.winfo_ismapped():
                    continue
                owns = page.owns_widget(event.widget)
                if not owns and not page.contains_pointer():
                    continue
                if owns and page.has_nested_scroll_target(event.widget):
                    return None
                if getattr(event, "num", None) == 4:
                    units = -3
                elif getattr(event, "num", None) == 5:
                    units = 3
                else:
                    delta = getattr(event, "delta", 0)
                    if not delta:
                        return None
                    units = -3 if delta > 0 else 3
                page.scroll_units(units)
                return "break"
            except tk.TclError:
                continue
        return None

    # ═══════════════════════════════════════════════════════════════
    # Tab Builders
    # ═══════════════════════════════════════════════════════════════

    def _build_project_tab(self, tab):
        content = ttk.Frame(tab, style="Surface.TFrame", padding=24)
        content.pack(fill=tk.X)
        ttk.Label(content, text="连接目标项目", style="Title.TLabel").grid(
            row=0, column=0, columnspan=3, sticky=tk.W
        )
        ttk.Label(
            content,
            text="Bridge 会只读检查 Git，然后在项目的 .bridge 目录保存协调数据。",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=(4, 20))

        ttk.Label(content, text="Git 项目文件夹", style="Section.TLabel").grid(
            row=2, column=0, columnspan=3, sticky=tk.W, pady=(0, 6)
        )
        ttk.Entry(content, textvariable=self.target_dir).grid(row=3, column=0, columnspan=2, sticky=tk.EW)
        ttk.Button(content, text="选择文件夹", style="Secondary.TButton",
                   command=self._browse_dir).grid(row=3, column=2, padx=(8, 0))

        ttk.Label(content, text="项目名称", style="Section.TLabel").grid(
            row=4, column=0, columnspan=3, sticky=tk.W, pady=(18, 6)
        )
        ttk.Entry(content, textvariable=self.project_name).grid(row=5, column=0, columnspan=3, sticky=tk.EW)

        policy = ttk.Frame(content, style="Surface.TFrame")
        policy.grid(row=6, column=0, columnspan=3, sticky=tk.EW, pady=(20, 0))
        ttk.Label(policy, text="推进方式", style="Surface.TLabel").grid(row=0, column=0, sticky=tk.W)
        ttk.Combobox(policy, textvariable=self.progression_policy,
                     values=("automatic", "hybrid", "manual"),
                     state="readonly", width=16).grid(row=1, column=0, sticky=tk.W, pady=(5, 0))
        ttk.Label(policy, text="安全确认", style="Surface.TLabel").grid(row=0, column=1, sticky=tk.W, padx=(24, 0))
        ttk.Combobox(policy, textvariable=self.safety_mode,
                     values=("strict", "balanced", "expert"),
                     state="readonly", width=16).grid(row=1, column=1, sticky=tk.W, padx=(24, 0), pady=(5, 0))
        ttk.Label(policy, text="推荐：hybrid + balanced", style="Muted.TLabel").grid(
            row=1, column=2, sticky=tk.W, padx=(24, 0), pady=(5, 0)
        )

        self.connect_button = ttk.Button(
            content, text="连接项目", style="Primary.TButton", command=self._init_coordinator
        )
        self.connect_button.grid(row=7, column=0, sticky=tk.W, pady=(24, 0))
        self.project_health_label = ttk.Label(content, text="尚未检查", style="Muted.TLabel")
        self.project_health_label.grid(row=7, column=1, columnspan=2, sticky=tk.W, padx=(14, 0), pady=(24, 0))
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)

        advanced = ttk.LabelFrame(tab, text="高级设置", padding=14)
        advanced.pack(fill=tk.X, pady=(14, 0))
        ttk.Label(advanced, text="数据库路径（通常无需修改）", style="Muted.TLabel").pack(anchor=tk.W)
        ttk.Entry(advanced, textvariable=self.db_path).pack(fill=tk.X, pady=(5, 0))

    def _build_agent_tab(self, tab):
        header = ttk.Frame(tab, style="App.TFrame")
        header.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(header, text="协作角色", style="PageTitle.TLabel").pack(anchor=tk.W)
        ttk.Label(header, text="配置执行与独立审查角色", style="PageMuted.TLabel").pack(
            anchor=tk.W, pady=(3, 0)
        )

        cards = ttk.Frame(tab, style="App.TFrame")
        cards.pack(fill=tk.X)
        cards.columnconfigure(0, weight=1)
        cards.columnconfigure(1, weight=1)

        a_frame = ttk.LabelFrame(cards, text="规划与审查 Agent", padding=16)
        a_frame.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 7))
        self._build_agent_fields(a_frame, self.agent_a_name, self.agent_a_role, self.agent_a_model)

        b_frame = ttk.LabelFrame(cards, text="执行 Agent", padding=16)
        b_frame.grid(row=0, column=1, sticky=tk.NSEW, padx=(7, 0))
        self._build_agent_fields(b_frame, self.agent_b_name, self.agent_b_role, self.agent_b_model)

        optional = ttk.Frame(tab, style="App.TFrame")
        optional.pack(fill=tk.X, pady=(12, 0))
        self.agent_optional_frame = optional
        self.c_toggle_btn = ttk.Button(optional, text="添加第三个执行 Agent",
                                       style="Secondary.TButton", command=self._toggle_agent_c)
        self.c_toggle_btn.pack(side=tk.LEFT)
        self.c_frame = ttk.LabelFrame(tab, text="第三个执行 Agent", padding=16)
        self._build_agent_fields(self.c_frame, self.agent_c_name, self.agent_c_role, self.agent_c_model)

        registered = ttk.LabelFrame(tab, text="已登记 Agent", padding=12)
        registered.pack(fill=tk.BOTH, expand=True, pady=(14, 0))
        self.agent_tree = ttk.Treeview(
            registered, columns=("name", "role", "model", "status"), show="headings", height=5
        )
        for key, label, width in (
            ("name", "名称", 180), ("role", "职责", 220),
            ("model", "模型档案", 220), ("status", "状态", 80),
        ):
            self.agent_tree.heading(key, text=label)
            self.agent_tree.column(key, width=width, anchor=tk.W)
        self.agent_tree.pack(fill=tk.BOTH, expand=True)
        ttk.Button(
            registered, text="保存 Agent 配置", style="Primary.TButton",
            command=self._register_agents_to_coordinator,
        ).pack(anchor=tk.E, pady=(10, 0))

    def _build_agent_fields(self, parent, name_var, role_var, model_var):
        for row, (label, var) in enumerate((
            ("显示名称", name_var), ("职责说明", role_var), ("模型档案", model_var),
        )):
            ttk.Label(parent, text=label).grid(row=row * 2, column=0, sticky=tk.W, pady=(0, 4))
            ttk.Entry(parent, textvariable=var).grid(row=row * 2 + 1, column=0, sticky=tk.EW, pady=(0, 10))
        parent.columnconfigure(0, weight=1)

    def _build_coordinator_tab(self, tab):
        top = ttk.Frame(tab, style="App.TFrame")
        top.pack(fill=tk.X, pady=(0, 10))
        self.summary_label = ttk.Label(top, text="请先连接项目", style="PageTitle.TLabel")
        self.summary_label.pack(side=tk.LEFT)
        ttk.Button(top, text="新建任务", style="Primary.TButton",
                   command=self._show_create_task_dialog).pack(side=tk.RIGHT)
        ttk.Button(top, text="📊 DAG 看板", style="Secondary.TButton",
                   command=self._gui_view_dag_board).pack(side=tk.RIGHT, padx=(0, 8))

        main = ttk.Panedwindow(tab, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True)

        list_panel = ttk.Frame(main, style="Surface.TFrame", padding=10, width=420)
        detail_panel = ttk.Frame(main, style="Surface.TFrame", padding=18)
        main.add(list_panel, weight=2)
        main.add(detail_panel, weight=3)

        ttk.Label(list_panel, text="任务", style="Section.TLabel").pack(anchor=tk.W, pady=(0, 8))
        tree_wrap = ttk.Frame(list_panel, style="Surface.TFrame")
        tree_wrap.pack(fill=tk.BOTH, expand=True)
        self.task_tree = ttk.Treeview(
            tree_wrap, columns=("title", "state", "owner"), show="headings", height=14
        )
        self.task_tree.heading("title", text="标题")
        self.task_tree.heading("state", text="状态")
        self.task_tree.heading("owner", text="执行者")
        self.task_tree.column("title", width=190, anchor=tk.W)
        self.task_tree.column("state", width=90, anchor=tk.W)
        self.task_tree.column("owner", width=100, anchor=tk.W)
        self.task_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(tree_wrap, orient=tk.VERTICAL, command=self.task_tree.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.task_tree.configure(yscrollcommand=scroll.set)
        self.task_tree.bind("<<TreeviewSelect>>", self._on_task_select)
        self.task_tree.bind("<Button-3>", self._show_task_context_menu)
        self.task_tree.bind("<Button-2>", self._show_task_context_menu)
        tree_wrap.bind("<Configure>", self._resize_task_columns)

        self.task_title_label = ttk.Label(detail_panel, text="请选择任务", style="Title.TLabel")
        self.task_title_label.pack(anchor=tk.W)
        self.task_meta_label = ttk.Label(detail_panel, text="", style="Muted.TLabel")
        self.task_meta_label.pack(anchor=tk.W, pady=(4, 4))
        self.task_dep_label = ttk.Label(detail_panel, text="", style="Muted.TLabel")
        self.task_dep_label.pack(anchor=tk.W, pady=(0, 10))

        progress = ttk.Frame(detail_panel, style="Surface.TFrame")
        progress.pack(fill=tk.X, pady=(0, 16))
        self.stage_labels = []
        for index, name in enumerate(("准备", "分配", "执行", "验证", "审查", "合并", "完成")):
            label = ttk.Label(progress, text=name, anchor=tk.CENTER, padding=(5, 7))
            label.grid(row=0, column=index, sticky=tk.EW, padx=(0 if index == 0 else 2, 0))
            progress.columnconfigure(index, weight=1, uniform="stage")
            self.stage_labels.append(label)

        self.task_goal_label = ttk.Label(
            detail_panel, text="从左侧选择任务查看目标和操作。", style="Surface.TLabel",
            justify=tk.LEFT, wraplength=650,
        )
        self.task_goal_label.pack(fill=tk.X, anchor=tk.W, pady=(0, 14))

        assignment = ttk.LabelFrame(detail_panel, text="任务角色", padding=10)
        assignment.pack(fill=tk.X, pady=(0, 14))
        ttk.Label(assignment, text="执行者").grid(row=0, column=0, sticky=tk.W)
        self.owner_combo = ttk.Combobox(assignment, textvariable=self.owner_choice,
                                        state="readonly", width=24)
        self.owner_combo.grid(row=1, column=0, sticky=tk.EW, pady=(4, 0), padx=(0, 8))
        ttk.Label(assignment, text="独立审查者").grid(row=0, column=1, sticky=tk.W)
        self.reviewer_combo = ttk.Combobox(assignment, textvariable=self.reviewer_choice,
                                           state="readonly", width=24)
        self.reviewer_combo.grid(row=1, column=1, sticky=tk.EW, pady=(4, 0))
        assignment.columnconfigure(0, weight=1)
        assignment.columnconfigure(1, weight=1)

        action_frame = ttk.Frame(detail_panel, style="Surface.TFrame", padding=(0, 6))
        action_frame.pack(fill=tk.X)
        self.action_title_label = ttk.Label(action_frame, text="下一步", style="Section.TLabel")
        self.action_title_label.pack(anchor=tk.W)
        self.action_desc_label = ttk.Label(
            action_frame, text="请选择一个任务", style="Muted.TLabel", wraplength=650
        )
        self.action_desc_label.pack(anchor=tk.W, pady=(4, 10))
        self.primary_action_button = ttk.Button(
            action_frame, text="请选择任务", style="Primary.TButton",
            command=self._run_primary_action, state=tk.DISABLED,
        )
        self.primary_action_button.pack(side=tk.LEFT)
        self.copy_prompt_button = ttk.Button(
            action_frame, text="复制提示词", style="Secondary.TButton",
            command=self._copy_current_prompt, state=tk.DISABLED,
        )
        self.copy_prompt_button.pack(side=tk.LEFT, padx=(8, 0))
        self.open_workspace_button = ttk.Button(
            action_frame, text="打开工作区", style="Secondary.TButton",
            command=self._open_current_workspace, state=tk.DISABLED,
        )
        self.open_workspace_button.pack(side=tk.LEFT, padx=(8, 0))
        self.view_diff_button = ttk.Button(
            action_frame, text="查看变更 (Diff)", style="Secondary.TButton",
            command=self._gui_view_task_diff, state=tk.DISABLED,
        )
        self.view_diff_button.pack(side=tk.LEFT, padx=(8, 0))
        self.ai_review_button = ttk.Button(
            action_frame, text="AI 预审", style="Secondary.TButton",
            command=self._gui_ai_pre_review, state=tk.DISABLED,
        )
        self.ai_review_button.pack(side=tk.LEFT, padx=(8, 0))
        self.conflict_ai_button = ttk.Button(
            action_frame, text="⚡ 冲突诊断", style="Secondary.TButton",
            command=self._gui_ai_conflict_diagnosis, state=tk.DISABLED,
        )
        self.conflict_ai_button.pack(side=tk.LEFT, padx=(8, 0))


        activity = ttk.LabelFrame(detail_panel, text="活动记录", padding=8)
        activity.pack(fill=tk.BOTH, expand=True, pady=(16, 0))
        self.preview_text = scrolledtext.ScrolledText(
            activity, height=7, font=("Consolas", 9), relief=tk.FLAT,
            background="#f7f8fa", foreground="#25313c",
        )
        self.preview_text.pack(fill=tk.BOTH, expand=True)
        detail_panel.bind("<Configure>", self._resize_task_detail)

        # Compatibility target for older helpers; queue details are now shown in task context.
        self.merge_label = ttk.Label(detail_panel, text="")

    def _resize_task_columns(self, event):
        available = max(event.width - 18, 210)
        self.task_tree.column("title", width=max(int(available * 0.48), 100), minwidth=90)
        self.task_tree.column("state", width=max(int(available * 0.27), 62), minwidth=58)
        self.task_tree.column("owner", width=max(int(available * 0.25), 58), minwidth=54)

    def _resize_task_detail(self, event):
        wrap = max(event.width - 42, 280)
        self.task_goal_label.configure(wraplength=wrap)
        self.action_desc_label.configure(wraplength=wrap)

    def _build_tools_tab(self, tab):
        """Keep optional document and LLM features away from the core task path."""
        notebook = ttk.Notebook(tab)
        notebook.pack(fill=tk.BOTH, expand=True)

        docs_tab = ttk.Frame(notebook, padding=20)
        notebook.add(docs_tab, text="协作文档")
        ttk.Label(docs_tab, text="生成项目级协作文档", style="PageTitle.TLabel").pack(anchor=tk.W)
        ttk.Label(
            docs_tab,
            text="任务执行时 Bridge 会自动生成专用任务包。这里仅用于需要长期协作说明的项目。",
            style="PageMuted.TLabel",
        ).pack(anchor=tk.W, pady=(4, 18))
        ttk.Label(docs_tab, text="协作模板", style="Section.TLabel").pack(anchor=tk.W)
        mode_values = list(TEMPLATES.keys())
        ttk.Combobox(docs_tab, textvariable=self.mode, values=mode_values,
                     state="readonly", width=28).pack(anchor=tk.W, pady=(6, 16))
        buttons = ttk.Frame(docs_tab)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="预览", style="Secondary.TButton",
                   command=self._preview).pack(side=tk.LEFT)
        ttk.Button(buttons, text="生成到项目", style="Primary.TButton",
                   command=self._generate).pack(side=tk.LEFT, padx=(8, 0))

        llm_tab = ttk.Frame(notebook, padding=20)
        notebook.add(llm_tab, text="LLM 辅助")
        self._build_llm_tab(llm_tab)

        pipeline_tab = ttk.Frame(notebook, padding=20)
        notebook.add(pipeline_tab, text="自定义流水线")
        self._build_pipeline_tab(pipeline_tab)

        self.mode_frames = {}

    def _build_llm_tab(self, tab):
        ttk.Checkbutton(tab, text="启用 LLM API 辅助理解需求",
                        variable=self.llm_enabled).pack(anchor=tk.W, pady=(0, 10))

        llm_frame = ttk.LabelFrame(tab, text="API 设置", padding=10)
        llm_frame.pack(fill=tk.X, pady=(0, 15))

        fields = [
            ("API Key", self.llm_api_key, True),
            ("API Base URL", self.llm_api_base, False),
            ("模型", self.llm_model, False),
        ]
        for i, (label, var, is_secret) in enumerate(fields):
            ttk.Label(llm_frame, text=label, width=12).grid(row=i, column=0, sticky=tk.W, pady=3)
            ttk.Entry(llm_frame, textvariable=var, width=50,
                      show="*" if is_secret else "").grid(row=i, column=1, sticky=tk.EW, padx=5)
        llm_frame.columnconfigure(1, weight=1)

        ttk.Label(tab, text="描述你的项目需求（LLM 将辅助分析并自动填充配置）：",
                  font=("", 9)).pack(anchor=tk.W, pady=(10, 5))
        self.llm_input_text = scrolledtext.ScrolledText(tab, height=4, width=60)
        self.llm_input_text.pack(fill=tk.X, pady=(0, 10))

        llm_btn_frame = ttk.Frame(tab)
        llm_btn_frame.pack(fill=tk.X)
        ttk.Button(llm_btn_frame, text="分析需求", style="Primary.TButton",
                   command=self._llm_analyze).pack(side=tk.LEFT, padx=(0, 10))
        self.llm_status = ttk.Label(llm_btn_frame, text="", foreground="gray")
        self.llm_status.pack(side=tk.LEFT)

    def _build_pipeline_tab(self, tab):
        ttk.Label(tab, text="仅「Custom」模式下生效。用上下按钮调整顺序。",
                  font=("", 9), foreground="gray").pack(anchor=tk.W, pady=(0, 10))

        pipeline_edit_frame = ttk.Frame(tab)
        pipeline_edit_frame.pack(fill=tk.BOTH, expand=True)

        list_frame = ttk.Frame(pipeline_edit_frame)
        list_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        ttk.Label(list_frame, text="流水线阶段", font=("", 9, "bold")).pack(anchor=tk.W)
        self.pipeline_listbox = tk.Listbox(list_frame, height=12, selectmode=tk.SINGLE)
        self.pipeline_listbox.pack(fill=tk.BOTH, expand=True, pady=5)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.pipeline_listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.pipeline_listbox.config(yscrollcommand=scrollbar.set)

        edit_frame = ttk.Frame(pipeline_edit_frame)
        edit_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        stage_fields = [("阶段名称", "stage_name"), ("执行 Agent", "stage_agent"), ("描述", "stage_desc")]
        self.stage_vars = {}
        for label, key in stage_fields:
            ttk.Label(edit_frame, text=label, font=("", 9)).pack(anchor=tk.W, pady=(5, 0))
            var = tk.StringVar()
            self.stage_vars[key] = var
            ttk.Entry(edit_frame, textvariable=var, width=30).pack(fill=tk.X)

        btn_row = ttk.Frame(edit_frame)
        btn_row.pack(fill=tk.X, pady=10)
        ttk.Button(btn_row, text="添加", command=self._add_stage).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="更新", command=self._update_stage).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="删除", command=self._delete_stage).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="上移", command=self._move_stage_up).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="下移", command=self._move_stage_down).pack(side=tk.LEFT, padx=2)

        self.pipeline_listbox.bind("<<ListboxSelect>>", self._on_stage_select)

    # ═══════════════════════════════════════════════════════════════
    # Coordinator Operations
    # ═══════════════════════════════════════════════════════════════

    def _init_coordinator(self):
        """Initialize BridgeCoordinator and the SQLite database."""
        raw_target = self.target_dir.get().strip()
        if not raw_target:
            messagebox.showerror("错误", "请先选择目标项目文件夹")
            return
        target = os.path.realpath(raw_target)
        if not os.path.isdir(target):
            messagebox.showerror("无法连接", f"项目文件夹不存在：\n{target}")
            return
        git_ok, git_message = _check_git_repo(target)
        if not git_ok:
            self.project_health_label.config(text=git_message, foreground="#a32626")
            messagebox.showerror("Git 项目不可用", git_message)
            return
        git_status = GitRepositoryAdapter(target).check_repo()
        if not git_status.head_commit:
            messagebox.showerror("Git 项目不可用", "项目还没有初始提交。请先创建一次 commit。")
            return
        self.target_dir.set(target)

        # A reconnect must release the previous coordinator before opening a
        # new database connection.  Keeping the old lock alive makes a
        # perfectly valid reconnect look like a second Bridge instance.
        if self.coordinator is not None:
            try:
                self.coordinator.close()
            except Exception as exc:
                logger.exception("Failed to close previous Bridge coordinator: %s", exc)
            finally:
                self.coordinator = None
                self.db = None
        elif self.db is not None:
            try:
                self.db.release_lock()
                self.db.close()
            except Exception as exc:
                logger.exception("Failed to close previous Bridge database: %s", exc)
            finally:
                self.db = None

        db_path = self.db_path.get().strip()
        if not db_path:
            bridge_dir = os.path.join(target, ".bridge")
            os.makedirs(bridge_dir, exist_ok=True)
            db_path = os.path.join(bridge_dir, "bridge.db")
            self.db_path.set(db_path)

        try:
            self.db = init_database(db_path)
            if not self.db.acquire_lock():
                self.db.close()
                self.db = None
                raise CoordinatorError(
                    f"已有 Bridge 实例正在使用数据库：{db_path}"
                )
            self.coordinator = BridgeCoordinator(database=self.db)

            # Find or initialize the project.
            project_name = self.project_name.get() or os.path.basename(target) or "Bridge Project"
            existing_projects = self.db.list_projects()
            pid = None
            for p in existing_projects:
                if os.path.realpath(p.get("root_path", "")) == target:
                    pid = p["id"]
                    self.progression_policy.set(
                        p.get("progression_policy", "hybrid")
                    )
                    self.safety_mode.set(
                        p.get("confirmation_policy", "balanced")
                    )
                    self._log(f"找到已有项目: {pid}")
                    break

            if not pid:
                pid = self.coordinator.init_project(
                    name=project_name,
                    root_path=target,
                    language="zh-CN" if self.lang == "zh" else "en",
                    progression=self.progression_policy.get(),
                    confirmation=self.safety_mode.get(),
                )
                self._log(f"创建新项目: {pid}")

            self.current_project_id.set(pid)
            self._apply_project_policies(show_message=False)
            self.project_health_label.config(
                text=f"Git 就绪 · {git_status.current_branch} · {git_status.head_commit[:8]}",
                foreground="#227447",
            )
            self.connection_label.config(text=f"已连接 · {project_name}", foreground="#8bd3a8")
            self.connect_button.config(text="重新连接")
            self._log(f"已连接项目：{project_name}\n数据库：{db_path}")
            self._refresh_coordinator()
            self.notebook.select(1)
        except Exception as e:
            if self.coordinator is not None:
                try:
                    self.coordinator.close()
                except Exception:
                    logger.exception("Failed to clean up coordinator after init failure")
                self.coordinator = None
            elif self.db is not None:
                try:
                    self.db.release_lock()
                    self.db.close()
                except Exception:
                    logger.exception("Failed to clean up database after init failure")
            self.db = None
            self._log(f"初始化失败: {e}")
            messagebox.showerror("初始化失败", str(e))

    def _apply_project_policies(self, show_message=True):
        """Apply user-selected progression and dangerous-action policies."""
        if not self.coordinator or not self.current_project_id.get():
            messagebox.showerror("错误", "请先初始化协调器")
            return
        project_id = self.current_project_id.get()
        try:
            self.db.update_project(
                project_id,
                progression_policy=self.progression_policy.get(),
            )
            self.coordinator.set_safety_mode(
                project_id, self.safety_mode.get(),
            )
            self._log(
                "策略已更新: "
                f"progression={self.progression_policy.get()}, "
                f"safety={self.safety_mode.get()}"
            )
            if show_message:
                messagebox.showinfo("成功", "项目策略已更新")
        except Exception as e:
            messagebox.showerror("策略更新失败", str(e))

    def _register_agents_to_coordinator(self):
        """Register configured agents in the coordinator database."""
        if not self.coordinator or not self.current_project_id.get():
            messagebox.showerror("错误", "请先初始化协调器")
            return

        pid = self.current_project_id.get()
        agents = self._get_agent_configs()
        registered = []

        try:
            existing_agents = self.db.list_agents(pid)
            existing_names = {a["display_name"]: a["id"] for a in existing_agents}

            for i, agent in enumerate(agents):
                if agent["name"] in existing_names:
                    aid = existing_names[agent["name"]]
                    roles = ["planner", "reviewer"] if i == 0 else ["implementer"]
                    permissions = {
                        "can_plan": i == 0,
                        "can_review": i == 0,
                        "can_run_validation": i != 0,
                    }
                    self.db.update_agent(
                        aid,
                        model=agent["model"],
                        roles_json=json.dumps(roles),
                        permissions_json=json.dumps(permissions),
                        enabled=1,
                    )
                    registered.append({"id": aid, "name": agent["name"], "role": agent["role"],
                                       "model": agent["model"]})
                    continue

                roles = []
                perms = {}
                if i == 0:
                    roles = ["planner", "reviewer"]
                    perms = {"can_plan": True, "can_review": True}
                elif i == 1:
                    roles = ["implementer"]
                    perms = {"can_run_validation": True}
                else:
                    roles = ["implementer"]
                    perms = {"can_run_validation": True}

                aid = self.coordinator.add_agent(
                    project_id=pid,
                    display_name=agent["name"],
                    model=agent["model"],
                    capability_tier="high" if i == 0 else "standard",
                    cost_tier="high" if i == 0 else "low",
                    roles=roles,
                    **perms,
                )
                registered.append({"id": aid, "name": agent["name"], "role": agent["role"],
                                   "model": agent["model"]})

            self.dynamic_agents = registered
            self._log("Agent 配置已保存：" + "、".join(a["name"] for a in registered))
            self._refresh_coordinator()
            self.notebook.select(2)
        except Exception as e:
            self._log(f"注册 Agent 失败: {e}")
            messagebox.showerror("失败", str(e))

    def _show_create_task_dialog(self):
        if not self.coordinator:
            messagebox.showwarning("尚未连接", "请先在“项目”页连接 Git 项目。")
            self.notebook.select(0)
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("新建任务")
        dialog.geometry("620x560")
        dialog.minsize(560, 500)
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="新建任务", style="Title.TLabel").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(frame, text="任务标题").grid(row=1, column=0, sticky=tk.W, pady=(16, 4))
        title_entry = ttk.Entry(frame, textvariable=self.new_task_title)
        title_entry.grid(row=2, column=0, sticky=tk.EW)
        ttk.Label(frame, text="目标").grid(row=3, column=0, sticky=tk.W, pady=(14, 4))
        goal_text = tk.Text(frame, height=4, wrap=tk.WORD, font=("Microsoft YaHei UI", 9))
        goal_text.grid(row=4, column=0, sticky=tk.EW)
        goal_text.insert("1.0", self.new_task_goal.get())
        ttk.Label(frame, text="允许修改的路径（每行一个）").grid(row=5, column=0, sticky=tk.W, pady=(14, 4))
        paths_text = tk.Text(frame, height=4, wrap=tk.NONE, font=("Consolas", 9))
        paths_text.grid(row=6, column=0, sticky=tk.EW)
        paths_text.insert("1.0", self.new_task_paths.get())
        ttk.Label(frame, text="验收标准（每行一个）").grid(row=7, column=0, sticky=tk.W, pady=(14, 4))
        acceptance_text = tk.Text(frame, height=4, wrap=tk.WORD, font=("Microsoft YaHei UI", 9))
        acceptance_text.grid(row=8, column=0, sticky=tk.EW)
        acceptance_text.insert("1.0", self.new_task_acceptance.get())
        ttk.Label(frame, text="自动检查（每行一个，例如 unit-tests）").grid(
            row=9, column=0, sticky=tk.W, pady=(14, 4)
        )
        checks_entry = ttk.Entry(frame, textvariable=self.new_task_checks)
        checks_entry.grid(row=10, column=0, sticky=tk.EW)
        controls = ttk.Frame(frame)
        controls.grid(row=11, column=0, sticky=tk.E, pady=(18, 0))
        ttk.Button(controls, text="取消", style="Secondary.TButton", command=dialog.destroy).pack(side=tk.LEFT)

        def create():
            self.new_task_goal.set(goal_text.get("1.0", tk.END).strip())
            self.new_task_paths.set(paths_text.get("1.0", tk.END).strip())
            self.new_task_acceptance.set(acceptance_text.get("1.0", tk.END).strip())
            if self._create_task():
                dialog.destroy()

        ttk.Button(controls, text="创建任务", style="Primary.TButton", command=create).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        title_entry.focus_set()

    @staticmethod
    def _split_lines(value: str) -> list[str]:
        return [part.strip() for line in value.splitlines() for part in line.split(",") if part.strip()]

    def _create_task(self):
        """Create a task."""
        if not self.coordinator:
            messagebox.showerror("错误", "请先初始化协调器")
            return

        title = self.new_task_title.get().strip()
        if not title:
            messagebox.showerror("错误", "请输入任务标题")
            return False

        try:
            paths = self._split_lines(self.new_task_paths.get())
            acceptance = self._split_lines(self.new_task_acceptance.get())
            checks = self._split_lines(self.new_task_checks.get())
            if not paths or not acceptance:
                messagebox.showerror("任务信息不完整", "至少需要一个允许路径和一条验收标准。")
                return False
            pid = self.current_project_id.get()
            gid = self.coordinator.create_goal(pid, title=title)

            tid = self.coordinator.create_task(
                goal_id=gid, title=title,
                goal=self.new_task_goal.get(),
                allowed_paths=paths,
                forbidden_paths=[".bridge/**", ".git/**"],
                acceptance_criteria=acceptance,
                required_checks=checks,
                risk="medium",
            )
            self._log(f"已创建任务 {tid}：{title}")
            self.new_task_title.set("")
            self.new_task_goal.set("")
            self._refresh_coordinator()
            self._select_task(tid)
            return True
        except Exception as e:
            self._log(f"创建任务失败: {e}")
            messagebox.showerror("失败", str(e))
            return False

    def _selected_task(self) -> dict | None:
        if not self.coordinator:
            return None
        selection = self.task_tree.selection()
        task_id = selection[0] if selection else self.selected_task_id
        return self.coordinator.get_task(str(task_id)) if task_id else None

    def _select_task(self, task_id: str):
        if self.task_tree.exists(task_id):
            self.task_tree.selection_set(task_id)
            self.task_tree.focus(task_id)
            self.task_tree.see(task_id)
            self.selected_task_id = task_id
            self._on_task_select()

    def _on_task_select(self, _event=None):
        task = self._selected_task()
        if not task:
            return
        self.selected_task_id = task["id"]
        owner = self.coordinator.get_agent(task.get("owner_agent_id", ""))
        reviewer = self.coordinator.get_agent(task.get("reviewer_agent_id", ""))
        if owner:
            self.owner_choice.set(owner["display_name"])
        if reviewer:
            self.reviewer_choice.set(reviewer["display_name"])

        state = task["state"]
        pending = any(
            review.get("task_id") == task["id"]
            for review in self.db.list_pending_reviews(self.current_project_id.get())
        )
        self._task_action = get_next_task_action(state, pending)
        self.task_title_label.config(text=task["title"])
        total_tokens = self.coordinator.costs.tokens_by_task(task["id"]) if self.coordinator else 0
        total_cost = self.coordinator.costs.cost_by_task(task["id"]) if self.coordinator else 0.0
        cost_str = f"  ·  消耗 {total_tokens} tokens (${total_cost:.3f})" if total_tokens > 0 else ""
        self.task_meta_label.config(
            text=f"{task['id']}  ·  {STATE_LABELS.get(state, state)}  ·  风险 {task.get('risk', 'medium')}{cost_str}"
        )

        # Smart routing recommendation if task is ready and choices are available
        if state == TaskState.READY.value and self.coordinator:
            try:
                rec = self.coordinator.recommend_agent_for_task(task["id"], role="implementer")
                if rec and rec.get("agent_id"):
                    for agent in self.coordinator.list_agents(self.current_project_id.get()):
                        if agent["id"] == rec["agent_id"] and agent.get("display_name") in self.owner_combo["values"]:
                            self.owner_choice.set(agent["display_name"])
                            break
            except Exception:
                pass
        goal = task.get("goal") or "未填写补充说明"
        paths = ", ".join(json.loads(task.get("allowed_paths_json", "[]") or "[]"))
        self.task_goal_label.config(text=f"目标\n{goal}\n\n允许路径\n{paths or '-'}")
        self.action_title_label.config(text=self._task_action.label)
        self.action_desc_label.config(text=self._task_action.description)
        self.primary_action_button.config(
            text=self._task_action.label,
            state=tk.NORMAL if self._task_action.enabled else tk.DISABLED,
        )
        stage = WORKFLOW_STAGE.get(state, -1)
        for index, label in enumerate(self.stage_labels):
            if index < stage:
                label.config(background="#dcefe5", foreground="#25633f")
            elif index == stage:
                label.config(background="#d9ecf8", foreground="#0b5f93")
            else:
                label.config(background="#edf0f2", foreground="#6a747d")

        ws = self.coordinator.get_worktree(task["id"])
        can_handoff = bool(ws and ws.get("worktree_path"))
        self.open_workspace_button.config(state=tk.NORMAL if can_handoff else tk.DISABLED)
        self.copy_prompt_button.config(
            state=tk.NORMAL if can_handoff or state == TaskState.VALIDATING.value else tk.DISABLED
        )
        has_diff = can_handoff or state in (TaskState.SUBMITTED.value, TaskState.VALIDATING.value, TaskState.APPROVED.value, TaskState.DONE.value)
        self.view_diff_button.config(state=tk.NORMAL if has_diff else tk.DISABLED)
        self.ai_review_button.config(state=tk.NORMAL)

        # Update dependency status indicator
        if self.coordinator:
            try:
                dep_info = self.coordinator.get_task_dependency_status(task["id"])
                if dep_info.get("total_dependencies", 0) > 0:
                    if dep_info.get("met"):
                        self.task_dep_label.config(
                            text=f"🔗 前置依赖已满足 ({', '.join(dep_info.get('dependencies', []))})",
                            foreground="#1a7f37"
                        )
                    elif dep_info.get("has_cancelled_parent"):
                        self.task_dep_label.config(
                            text=f"🚫 依赖永久阻塞: 前置任务已取消 ({', '.join(dep_info.get('unmet_dependencies', []))})",
                            foreground="#cf222e"
                        )
                    else:
                        self.task_dep_label.config(
                            text=f"⏳ 等待前置任务完成: {', '.join(dep_info.get('unmet_dependencies', []))}",
                            foreground="#d97706"
                        )
                else:
                    self.task_dep_label.config(text="✅ 无前置依赖 (独立就绪)", foreground="#65717d")
            except Exception:
                self.task_dep_label.config(text="")
        else:
            self.task_dep_label.config(text="")

        # Update conflict & auto-fix diagnosis button
        is_troubled = state in (
            TaskState.BLOCKED.value, TaskState.CONFLICT.value,
            TaskState.REVISION_REQUIRED.value, TaskState.ESCALATED.value,
            TaskState.VALIDATING.value, TaskState.SUBMITTED.value
        )
        self.conflict_ai_button.config(state=tk.NORMAL if is_troubled else tk.DISABLED)


    def _run_primary_action(self):
        task = self._selected_task()
        if not task:
            messagebox.showwarning("请选择任务", "请先从左侧选择一个任务。")
            return
        action = self._task_action.key
        handlers = {
            "prepare": self._prepare_selected_task,
            "assign": self._gui_assign_task,
            "start": self._gui_start_attempt,
            "submit": self._import_submission,
            "validate": self._gui_validate_and_review,
            "review": self._record_review_result,
            "merge": self._merge_selected_task,
            "retry": self._retry_selected_task,
        }
        handler = handlers.get(action)
        if handler:
            handler()

    @staticmethod
    def _needs_confirmation(error: Exception) -> bool:
        message = str(error).lower()
        return "requires confirmation" in message or "confirmed=true" in message

    def _transition_with_confirmation(self, task_id: str, state: TaskState, actor="user") -> bool:
        coordinator = self._require_coordinator()
        try:
            coordinator.transition_task(task_id, state, actor=actor, confirmed=False)
            return True
        except CoordinatorError as error:
            if not self._needs_confirmation(error):
                raise
            if not messagebox.askyesno(
                "需要确认", f"确认将任务推进到“{STATE_LABELS.get(state.value, state.value)}”？\n\n{error}",
            ):
                return False
            coordinator.transition_task(task_id, state, actor=actor, confirmed=True)
            return True

    def _prepare_selected_task(self):
        coordinator = self._require_coordinator()
        task = self._selected_task()
        if not task:
            return
        try:
            if task["state"] == TaskState.DRAFT.value:
                coordinator.transition_task(task["id"], TaskState.PLANNING, actor="user")
            if not self._transition_with_confirmation(task["id"], TaskState.READY):
                return
            self._log(f"任务 {task['id']} 已准备，可以分配 Agent。")
            self._refresh_coordinator()
        except Exception as error:
            messagebox.showerror("任务准备失败", str(error))

    def _agent_id_from_choice(self, display_name: str) -> str:
        coordinator = self._require_coordinator()
        for agent in coordinator.list_agents(self.current_project_id.get()):
            if agent.get("display_name") == display_name:
                return agent["id"]
        return ""

    def _default_agent_choices(self):
        agents = self.coordinator.list_agents(self.current_project_id.get()) if self.coordinator else []
        implementers = []
        reviewers = []
        for agent in agents:
            roles = json.loads(agent.get("roles_json", "[]") or "[]")
            permissions = json.loads(agent.get("permissions_json", "{}") or "{}")
            if agent.get("enabled") and "implementer" in roles:
                implementers.append(agent["display_name"])
            if agent.get("enabled") and permissions.get("can_review"):
                reviewers.append(agent["display_name"])
        self.owner_combo["values"] = implementers
        self.reviewer_combo["values"] = reviewers
        if implementers and self.owner_choice.get() not in implementers:
            self.owner_choice.set(implementers[0])
        if reviewers and self.reviewer_choice.get() not in reviewers:
            self.reviewer_choice.set(reviewers[0])

    def _task_package_dir(self, task_id: str) -> str:
        ws = self.coordinator.get_worktree(task_id) if self.coordinator else None
        attempt = self.coordinator.get_current_attempt(task_id) if self.coordinator else None
        if not ws or not ws.get("worktree_path") or not attempt:
            return ""
        return f"{ws['worktree_path']}.bridge-task-a{attempt['attempt_number']}"

    def _generate_handoff_package(self, task: dict, lease, attempt: dict, workspace: dict) -> str:
        package_dir = f"{workspace['worktree_path']}.bridge-task-a{attempt['attempt_number']}"
        package = generate_task_package(
            project_id=task.get("project_id", ""),
            task_id=task["id"],
            attempt=attempt["attempt_number"],
            lease_id=lease.lease_id,
            lease_expires_at=lease.expires_at.isoformat(),
            agent_id=task.get("owner_agent_id", ""),
            reviewer_agent_id=task.get("reviewer_agent_id", ""),
            title=task.get("title", ""),
            objective=task.get("goal", ""),
            risk=task.get("risk", "medium"),
            complexity=task.get("complexity", "medium"),
            base_commit=workspace.get("base_commit", ""),
            branch=workspace.get("branch", ""),
            allowed_paths=json.loads(task.get("allowed_paths_json", "[]") or "[]"),
            forbidden_paths=json.loads(task.get("forbidden_paths_json", "[]") or "[]"),
            acceptance_criteria=json.loads(task.get("acceptance_criteria_json", "[]") or "[]"),
            required_checks=json.loads(task.get("required_checks_json", "[]") or "[]"),
            completion={
                "receipt_path": os.path.join(package_dir, "RECEIPT.md"),
                "artifacts_path": os.path.join(package_dir, "ARTIFACTS.json"),
            },
        )
        package.write_to(package_dir)
        self.last_handoff_path = package_dir
        return package_dir

    def _build_implementer_prompt(self, task: dict) -> str:
        coordinator = self._require_coordinator()
        ws = coordinator.get_worktree(task["id"])
        package_dir = self._task_package_dir(task["id"])
        owner = coordinator.get_agent(task.get("owner_agent_id", "")) or {}
        return (
            f"你是 {owner.get('display_name', '执行 Agent')}，负责 Bridge 任务 {task['id']}。\n\n"
            f"工作目录：{ws.get('worktree_path', '') if ws else ''}\n"
            f"任务包目录：{package_dir}\n\n"
            "先阅读任务包中的 PROMPT.md、TASK.md、CONSTRAINTS.md 和 CHECKS.md。\n"
            "只修改允许路径；完成后运行测试并创建 Git commit。\n"
            "最后更新任务包中的 RECEIPT.md 和 ARTIFACTS.json，将状态改为 completed，"
            "填写 submission_commit。不要修改主分支或强制推送。"
        )

    def _build_review_prompt(self, task: dict) -> str:
        package_dir = self._task_package_dir(task["id"])
        artifacts = self._load_artifacts(task["id"], required=False)
        return (
            f"你是任务 {task['id']} 的独立审查 Agent。\n\n"
            f"执行者提交：{artifacts.get('submission_commit', '')}\n"
            f"任务包目录：{package_dir}\n\n"
            "请检查 TASK.md、RECEIPT.md、ARTIFACTS.json、该 commit 的 diff 和测试结果。"
            "重点检查正确性、安全性、路径越界、遗漏边界和验收标准。\n"
            "完成后明确回复 Approved 或 Revision Required，并给出简短理由。"
        )

    def _copy_text(self, text: str, message: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update_idletasks()
        self._log(message)

    def _copy_current_prompt(self):
        task = self._selected_task()
        if not task:
            return
        if task["state"] == TaskState.VALIDATING.value:
            self._copy_text(self._build_review_prompt(task), "审查提示词已复制。")
        else:
            self._copy_text(self._build_implementer_prompt(task), "执行提示词已复制。")

    def _open_current_workspace(self):
        task = self._selected_task()
        ws = self.coordinator.get_worktree(task["id"]) if task else None
        path = ws.get("worktree_path", "") if ws else ""
        if path and os.path.isdir(path):
            os.startfile(path)
        else:
            messagebox.showwarning("工作区不可用", "该任务还没有可打开的 worktree。")

    def _load_artifacts(self, task_id: str, required=True) -> dict:
        package_dir = self._task_package_dir(task_id)
        path = os.path.join(package_dir, "ARTIFACTS.json") if package_dir else ""
        if not path or not os.path.isfile(path):
            if required:
                raise CoordinatorError("未找到 ARTIFACTS.json。请让执行 Agent 完成任务包中的回执。")
            return {}
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)

    def _revoke_attempt_lease(self, attempt: dict, reason: str):
        coordinator = self._require_coordinator()
        lease_id = attempt.get("lease_id", "") if attempt else ""
        if not lease_id:
            return
        try:
            coordinator.leases.revoke(lease_id, reason=reason)
        except Exception:
            pass
        coordinator.db.update_lease_status(lease_id, "revoked", reason)

    def _gui_assign_task(self):
        if not self.coordinator:
            return
        task = self._selected_task()
        if not task:
            return
        owner_id = self._agent_id_from_choice(self.owner_choice.get())
        reviewer_id = self._agent_id_from_choice(self.reviewer_choice.get())
        if not owner_id or not reviewer_id:
            messagebox.showwarning("角色未配置", "请先保存 Agent 配置，并选择执行者和独立审查者。")
            self.notebook.select(1)
            return
        if owner_id == reviewer_id:
            messagebox.showerror("角色冲突", "执行者和审查者必须是不同的 Agent。")
            return
        if not messagebox.askyesno(
            "开始任务",
            f"将“{task['title']}”交给 {self.owner_choice.get()} 执行，"
            f"由 {self.reviewer_choice.get()} 独立审查？",
        ):
            return
        try:
            self.coordinator.assign_task(task["id"], owner_id, reviewer_id, confirmed=True)
            self._log(
                f"任务 {task['id']} 已分配给 {self.owner_choice.get()}，审查者为 {self.reviewer_choice.get()}。"
            )
            self._refresh_coordinator()
            self._select_task(task["id"])
            self._gui_start_attempt(confirmed=True)
        except Exception as e:
            messagebox.showerror("分配失败", str(e))

    def _gui_start_attempt(self, confirmed=False):
        if not self.coordinator:
            return
        task = self._selected_task()
        if not task:
            return
        tid = task["id"]
        owner_id = task.get("owner_agent_id")
        if not owner_id:
            messagebox.showwarning("提示", "请先分配任务")
            return
        if not confirmed and not messagebox.askyesno(
            "创建隔离工作区", "Bridge 将创建租约、attempt、Git worktree 和任务包。是否继续？"
        ):
            return
        lease = None
        attempt_id = ""
        workspace_created = False
        try:
            lease = self.coordinator.acquire_lease(tid, owner_id, ttl_seconds=7200)
            attempt_id = self.coordinator.create_attempt(tid, owner_id, lease.lease_id)
            attempt = self.coordinator.get_current_attempt(tid)
            attempt_number = attempt["attempt_number"] if attempt else 1
            git_status = self.coordinator.check_git_repo(task.get("project_id", ""))
            ws = self.coordinator.create_worktree(
                tid, owner_id, attempt=attempt_number,
                base_commit=git_status.get("head_commit", ""), confirmed=True,
            )
            workspace_created = True
            package_dir = self._generate_handoff_package(task, lease, attempt, ws)
            self.coordinator.transition_task(
                tid, TaskState.IN_PROGRESS, actor=owner_id, confirmed=True,
            )
            # Start background automatic receipt watcher
            if hasattr(self.coordinator, "start_receipt_watcher"):
                try:
                    def _make_auto_callback(target_tid):
                        return lambda t, res: (self.root.after(0, lambda: self._handle_auto_receipt(target_tid, res)) if self.root else None)
                    self.coordinator.start_receipt_watcher(
                        package_dir, tid, attempt_number,
                        lease.lease_id, owner_id,
                        on_imported=_make_auto_callback(tid),
                    )
                except Exception as w_err:
                    logger.warning(f"Could not start receipt watcher: {w_err}")
            self._copy_text(self._build_implementer_prompt(task), "执行提示词已复制到剪贴板。")
            self._log(
                f"任务 {tid} 已开始。\n工作区：{ws['worktree_path']}\n任务包：{package_dir}\n"
                "下一步：在执行 Agent 中打开工作区并粘贴提示词。"
            )
            self._refresh_coordinator()
            self._select_task(tid)
        except Exception as e:
            if workspace_created:
                try:
                    self.coordinator.remove_worktree(
                        tid, force=True, confirmed=True,
                    )
                except Exception as cleanup_error:
                    self._log(f"工作树回滚失败: {cleanup_error}")
            if attempt_id:
                try:
                    self.coordinator.complete_attempt(attempt_id, status="failed")
                except Exception as cleanup_error:
                    self._log(f"Attempt 回滚失败: {cleanup_error}")
            if lease is not None:
                try:
                    self.coordinator.leases.revoke(
                        lease.lease_id, reason="GUI attempt setup failed",
                    )
                    self.coordinator.db.update_lease_status(
                        lease.lease_id, "revoked", "GUI attempt setup failed",
                    )
                except Exception as cleanup_error:
                    self._log(f"租约回滚失败: {cleanup_error}")
            messagebox.showerror("执行失败", str(e))

    def _import_submission(self):
        task = self._selected_task()
        if not task:
            return
        attempt = self.coordinator.get_current_attempt(task["id"])
        package_dir = self._task_package_dir(task["id"])
        if not attempt or not package_dir:
            messagebox.showerror("提交不可用", "当前任务没有活跃 attempt 或任务包。")
            return
        receipt_path = os.path.join(package_dir, "RECEIPT.md")
        try:
            with open(receipt_path, "r", encoding="utf-8") as file:
                receipt = parse_receipt(file.read())
            if receipt.status != "completed":
                raise CoordinatorError(
                    f"Agent 回执状态仍为 {receipt.status}。完成代码和 commit 后请改为 completed。"
                )
            artifacts = self._load_artifacts(task["id"])
            if artifacts.get("submission_commit") != receipt.submission_commit:
                raise CoordinatorError("RECEIPT.md 与 ARTIFACTS.json 中的 submission_commit 不一致。")
            try:
                result = self.coordinator.import_receipt(
                    package_dir, task["id"], attempt["attempt_number"],
                    attempt.get("lease_id", ""), task.get("owner_agent_id", ""), confirmed=False,
                )
            except CoordinatorError as error:
                if not self._needs_confirmation(error) or not messagebox.askyesno(
                    "导入回执", f"确认导入 Agent 回执？\n\n{error}"
                ):
                    raise
                result = self.coordinator.import_receipt(
                    package_dir, task["id"], attempt["attempt_number"],
                    attempt.get("lease_id", ""), task.get("owner_agent_id", ""), confirmed=True,
                )
            self.coordinator.transition_task(task["id"], TaskState.SUBMITTED, actor="user")
            self._log(
                f"已检测到提交 {receipt.submission_commit[:10]}，回执"
                f"{'已导入' if result else '此前已导入'}。"
            )
            self._refresh_coordinator()
            self._select_task(task["id"])
        except Exception as error:
            messagebox.showerror("未能接收提交", str(error))

    def _gui_validate_and_review(self):
        if not self.coordinator:
            return
        task = self._selected_task()
        if not task:
            return
        tid = task["id"]
        reviewer_id = task.get("reviewer_agent_id")
        if not reviewer_id:
            messagebox.showwarning("提示", "任务没有审查者")
            return
        package_dir = self._task_package_dir(tid)
        artifacts_path = os.path.join(package_dir, "ARTIFACTS.json")
        try:
            if task.get("state") == TaskState.SUBMITTED.value:
                self.coordinator.transition_task(
                    tid, TaskState.VALIDATING, actor="validator",
                )
            elif task.get("state") != TaskState.VALIDATING.value:
                raise CoordinatorError(
                    "Task must be submitted or validating before validation"
                )
            self._log(f"正在任务 worktree 中验证 {tid}...")
            required_checks = json.loads(
                task.get("required_checks_json", "[]") or "[]"
            ) or ["unit-tests"]
            val_res = self.coordinator.run_validation(
                tid, [{"check_id": check_id} for check_id in required_checks],
            )
            self._log(f"验证结果: {val_res}")
            failed = [r for r in val_res if r.get("status") != "passed"]
            if failed:
                diag = None
                try:
                    diag = self.coordinator.diagnose_task_failure(tid)
                except Exception:
                    pass
                failed_str = ", ".join(r.get("check_id", "unknown") for r in failed)
                self._log(f"任务 {tid} 验证未通过: {failed_str}")
                try:
                    self.coordinator.transition_task(tid, TaskState.REVISION_REQUIRED, actor="validator")
                except Exception:
                    pass
                self._refresh_coordinator()
                self._select_task(tid)

                msg = f"任务 {tid} 验证检查未通过: {failed_str}"
                if diag and diag.root_cause:
                    msg += f"\n\n【AI 自愈诊断】\n原因：{diag.root_cause}"
                    if messagebox.askyesno("验证未通过 · 查看自愈建议", f"{msg}\n\n是否立即复制自愈提示词到剪贴板？"):
                        self._copy_text(diag.next_attempt_prompt, "自愈提示词已复制到剪贴板。")
                else:
                    messagebox.showwarning("验证未通过", f"{msg}\n\n任务已自动流转至「需要修改」状态。")
                return

            artifact_result = self.coordinator.validate_artifacts(tid, artifacts_path)
            if not artifact_result.get("valid"):
                raise CoordinatorError(
                    artifact_result.get("error")
                    or "; ".join(artifact_result.get("issues", []))
                    or "ARTIFACTS.json validation failed"
                )

            pending_reviews = [
                review for review in self.coordinator.db.list_pending_reviews()
                if review.get("task_id") == tid
                and review.get("reviewer_agent_id") == reviewer_id
            ]
            if pending_reviews:
                review_id = pending_reviews[0]["id"]
            else:
                artifact_data = self._load_artifacts(tid)
                receipt_path = os.path.join(package_dir, "RECEIPT.md")
                with open(receipt_path, "r", encoding="utf-8") as file:
                    receipt_content = file.read()
                review_id = self.coordinator.submit_review(
                    tid, reviewer_id,
                    ReviewPackage(
                        task_id=tid,
                        title=task.get("title", ""),
                        objective=task.get("goal", ""),
                        risk=task.get("risk", "medium"),
                        complexity=task.get("complexity", "medium"),
                        acceptance_criteria=json.loads(
                            task.get("acceptance_criteria_json", "[]") or "[]"
                        ),
                        changed_files=[
                            item.get("path", "") if isinstance(item, dict) else str(item)
                            for item in artifact_data.get("changed_files", [])
                        ],
                        check_evidence={r["check_id"]: r["status"] for r in val_res},
                        implementer_receipt=receipt_content,
                        known_risks=artifact_data.get("known_failures", []),
                    ),
                )
            self._copy_text(self._build_review_prompt(task), "验证通过，审查提示词已复制。")
            reviewer = self.coordinator.get_agent(reviewer_id) or {}
            self._log(
                f"任务 {tid} 验证通过，已交给 {reviewer.get('display_name', reviewer_id)} 审查。\n"
                "下一步：将剪贴板提示词发给审查 Agent，完成后记录结果。"
            )
            self._refresh_coordinator()
            self._select_task(tid)
        except Exception as e:
            current = self.coordinator.get_task(tid)
            if current and current.get("state") == TaskState.VALIDATING.value:
                try:
                    self.coordinator.transition_task(tid, TaskState.REVISION_REQUIRED, actor="validator")
                except Exception:
                    pass
            self._refresh_coordinator()
            self._select_task(tid)
            messagebox.showerror("验证/审查失败", str(e))

    def _record_review_result(self):
        task = self._selected_task()
        if not task:
            return
        pending = [
            review for review in self.db.list_pending_reviews(self.current_project_id.get())
            if review.get("task_id") == task["id"]
        ]
        if not pending:
            self._gui_validate_and_review()
            return
        verdict = messagebox.askyesnocancel(
            "记录独立审查",
            "审查 Agent 是否批准该提交？\n\n选择“是”记录 Approved；选择“否”要求修改。",
        )
        if verdict is None:
            return
        from tkinter import simpledialog
        summary = simpledialog.askstring(
            "审查说明", "填写简短审查说明（可留空）：", parent=self.root
        ) or ""
        reviewer_id = task.get("reviewer_agent_id", "")
        try:
            if verdict:
                self.coordinator.complete_review(
                    pending[0]["id"], ReviewVerdict.APPROVED,
                    summary=summary, reviewer_agent_id=reviewer_id,
                )
                if not self._transition_with_confirmation(
                    task["id"], TaskState.APPROVED, actor=reviewer_id
                ):
                    return
                self._log(f"任务 {task['id']} 已通过独立审查，可以安全合并。")
            else:
                self.coordinator.complete_review(
                    pending[0]["id"], ReviewVerdict.REVISION_REQUIRED,
                    summary=summary, reviewer_agent_id=reviewer_id,
                )
                self.coordinator.transition_task(
                    task["id"], TaskState.REVISION_REQUIRED, actor=reviewer_id
                )
                self._log(f"任务 {task['id']} 需要修改：{summary or '审查未通过'}")
            self._refresh_coordinator()
            self._select_task(task["id"])
        except Exception as error:
            messagebox.showerror("审查结果保存失败", str(error))

    def _show_task_context_menu(self, event):
        item = self.task_tree.identify_row(event.y)
        if item:
            self.task_tree.selection_set(item)
            self._select_task(item)
            menu = tk.Menu(self.root, tearoff=0)
            menu.add_command(label=T("ctx_view_diff", self.lang), command=self._gui_view_task_diff)
            menu.add_command(label=T("ctx_ai_review", self.lang), command=self._gui_ai_pre_review)
            menu.add_command(label=T("btn_conflict_ai", self.lang), command=self._gui_ai_conflict_diagnosis)
            menu.add_separator()
            menu.add_command(label=T("ctx_copy_id", self.lang), command=lambda: self._copy_text(item, "任务 ID 已复制。"))
            menu.add_command(label=T("ctx_copy_prompt", self.lang), command=self._copy_current_prompt)
            menu.add_command(label=T("ctx_open_ws", self.lang), command=self._open_current_workspace)
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

    def _gui_view_task_diff(self):
        task = self._selected_task()
        if not task:
            return
        tid = task["id"]

        # Prevent duplicate windows for the same task
        existing = self._diff_windows.get(tid)
        if existing and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return

        try:
            diff_data = self.coordinator.get_task_diff(tid)
            win = tk.Toplevel(self.root)
            self._diff_windows[tid] = win
            win.title(T("diff_window_title", self.lang, tid=tid))
            win.geometry("820x560")
            win.transient(self.root)
            win.bind("<Escape>", lambda e: win.destroy())

            top_f = ttk.Frame(win, padding=10)
            top_f.pack(fill=tk.X)
            files = ", ".join(diff_data.get("files_changed", [])) or T("diff_no_changes", self.lang)
            base_str = (diff_data.get("base_commit") or "")[:8]
            ttk.Label(
                top_f,
                text=T("diff_base_commit", self.lang, tid=tid, base=base_str or "HEAD"),
                font=("Segoe UI", 10, "bold")
            ).pack(anchor=tk.W)
            ttk.Label(
                top_f,
                text=T("diff_files_stat", self.lang, files=files, stats=diff_data.get("stats", "")),
                style="Muted.TLabel"
            ).pack(anchor=tk.W, pady=(2, 0))

            text = scrolledtext.ScrolledText(win, font=("Consolas", 9), relief=tk.FLAT)
            text.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

            text.tag_configure("add", foreground="#1a7f37")
            text.tag_configure("del", foreground="#cf222e")
            text.tag_configure("chunk", foreground="#0969da", font=("Consolas", 9, "bold"))
            text.tag_configure("header", font=("Consolas", 9, "bold"))
            text.tag_configure("warning", foreground="#d97706", font=("Consolas", 9, "bold"))

            raw_diff = diff_data.get("diff_text", "")
            diff_text = raw_diff or T("diff_no_changes", self.lang)
            lines = diff_text.splitlines()
            MAX_RENDER_LINES = 3000

            for line in lines[:MAX_RENDER_LINES]:
                if line.startswith("+") and not line.startswith("+++"):
                    text.insert(tk.END, line + "\n", "add")
                elif line.startswith("-") and not line.startswith("---"):
                    text.insert(tk.END, line + "\n", "del")
                elif line.startswith("@@"):
                    text.insert(tk.END, line + "\n", "chunk")
                elif line.startswith("diff ") or line.startswith("index "):
                    text.insert(tk.END, line + "\n", "header")
                else:
                    text.insert(tk.END, line + "\n")

            if len(lines) > MAX_RENDER_LINES:
                trunc_msg = T("diff_truncated", self.lang, total=len(lines), limit=MAX_RENDER_LINES)
                text.insert(tk.END, trunc_msg + "\n", "warning")

            text.config(state=tk.DISABLED)
        except Exception as e:
            messagebox.showerror("Diff 获取失败", str(e))

    def _gui_ai_pre_review(self):
        task = self._selected_task()
        if not task:
            return
        tid = task["id"]

        # Prevent duplicate windows for the same task
        existing = self._ai_review_windows.get(tid)
        if existing and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return

        win = tk.Toplevel(self.root)
        self._ai_review_windows[tid] = win
        win.title(T("ai_review_window_title", self.lang, tid=tid))
        win.geometry("780x540")
        win.transient(self.root)
        win.bind("<Escape>", lambda e: win.destroy())

        top_f = ttk.Frame(win, padding=10)
        top_f.pack(fill=tk.X)
        header_label = ttk.Label(
            top_f, text=T("ai_review_analyzing", self.lang),
            foreground="#0b5f93", font=("Segoe UI", 11, "bold")
        )
        header_label.pack(anchor=tk.W)
        summary_label = ttk.Label(top_f, text="", style="Muted.TLabel")
        summary_label.pack(anchor=tk.W, pady=(2, 0))

        text = scrolledtext.ScrolledText(win, font=("Segoe UI", 10), relief=tk.FLAT)
        text.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        text.insert(tk.END, T("ai_review_analyzing", self.lang))
        text.config(state=tk.DISABLED)

        def _worker():
            try:
                report = self.coordinator.generate_ai_pre_review(tid)
                def _update_ui():
                    if not win.winfo_exists():
                        return
                    verdict_color = "#1a7f37" if report.verdict == "approved" else "#cf222e"
                    header_label.config(
                        text=T("ai_review_verdict", self.lang, verdict=report.verdict.upper(), conf=int(report.confidence * 100)),
                        foreground=verdict_color
                    )
                    summary_label.config(text=report.summary)
                    text.config(state=tk.NORMAL)
                    text.delete("1.0", tk.END)
                    text.insert(tk.END, report.to_markdown())
                    text.config(state=tk.DISABLED)
                self.root.after(0, _update_ui)
            except Exception as err:
                error_text = str(err)
                def _show_err():
                    if not win.winfo_exists():
                        return
                    header_label.config(text="AI 预审失败", foreground="#cf222e")
                    summary_label.config(text=error_text)
                    text.config(state=tk.NORMAL)
                    text.delete("1.0", tk.END)
                    text.insert(tk.END, f"Error generating pre-review: {error_text}")
                    text.config(state=tk.DISABLED)
                self.root.after(0, _show_err)

        threading.Thread(target=_worker, daemon=True).start()

    def _gui_view_dag_board(self):
        """Display an interactive DAG topology and pipeline wave board."""
        if not self.coordinator or not self.current_project_id.get():
            messagebox.showinfo("提示", "请先连接或初始化一个项目。")
            return

        if self._dag_window and self._dag_window.winfo_exists():
            self._dag_window.lift()
            self._dag_window.focus_force()
            return

        win = tk.Toplevel(self.root)
        self._dag_window = win
        win.title(T("dag_window_title", self.lang))
        win.geometry("860x600")
        win.transient(self.root)
        win.bind("<Escape>", lambda e: win.destroy())

        top_f = ttk.Frame(win, padding=12)
        top_f.pack(fill=tk.X)
        ttk.Label(top_f, text="📊 项目任务依赖与 DAG 波次看板", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor=tk.W)
        sub_title = ttk.Label(top_f, text="计算任务拓扑分层，指引并行推进节奏。双击任务条目可直接在主界面定位。", style="Muted.TLabel")
        sub_title.pack(anchor=tk.W, pady=(2, 0))

        # Compute DAG waves/layers
        project_id = self.current_project_id.get()
        all_tasks = self.coordinator.list_tasks(project_id)
        if not all_tasks:
            empty_lbl = ttk.Label(win, text="当前项目暂无任务，请点击「新建任务」创建任务。", padding=20)
            empty_lbl.pack()
            return

        task_map = {t["id"]: t for t in all_tasks}
        parents_map = {}
        for tid in task_map:
            try:
                status = self.coordinator.get_task_dependency_status(tid)
                parents_map[tid] = status.get("dependencies", [])
            except Exception:
                parents_map[tid] = []

        memo = {}
        visiting = set()
        has_cycle = False
        cycle_tasks = set()

        def get_depth(node):
            nonlocal has_cycle
            if node in visiting:
                has_cycle = True
                cycle_tasks.add(node)
                return 0
            if node in memo:
                return memo[node]
            visiting.add(node)
            parents = parents_map.get(node, [])
            if not parents:
                depth = 0
            else:
                p_depths = [get_depth(p) for p in parents if p in task_map]
                depth = (max(p_depths) + 1) if p_depths else 0
            visiting.remove(node)
            memo[node] = depth
            return depth

        for tid in task_map:
            get_depth(tid)

        layers = {}
        for tid, t in task_map.items():
            if tid in cycle_tasks:
                d = -1
            else:
                d = memo.get(tid, 0)
            layers.setdefault(d, []).append(t)

        tree_frame = ttk.Frame(win, padding=12)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        tree = ttk.Treeview(tree_frame, columns=("state", "owner", "deps"), show="tree headings", selectmode="browse")
        tree.heading("#0", text="波次 / 任务名称")
        tree.heading("state", text="状态")
        tree.heading("owner", text="执行者")
        tree.heading("deps", text="前置依赖")

        tree.column("#0", width=340, anchor=tk.W)
        tree.column("state", width=120, anchor=tk.W)
        tree.column("owner", width=110, anchor=tk.W)
        tree.column("deps", width=220, anchor=tk.W)

        scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        sorted_layer_keys = sorted([k for k in layers.keys() if k >= 0])
        for layer_idx in sorted_layer_keys:
            layer_node = tree.insert("", tk.END, text=f"🌊 波次 {layer_idx} (Layer {layer_idx})", open=True)
            for t in layers[layer_idx]:
                tid = t["id"]
                st = t.get("state", "draft")
                st_label = STATE_LABELS.get(st, st)
                owner_id = t.get("owner_agent_id", "")
                agent_info = self.coordinator.get_agent(owner_id) if owner_id else None
                owner_name = agent_info["display_name"] if agent_info else "-"
                deps = ", ".join(parents_map.get(tid, [])) or "无"
                tree.insert(layer_node, tk.END, iid=tid, text=f"[{tid}] {t['title']}",
                            values=(st_label, owner_name, deps))

        if -1 in layers:
            cycle_node = tree.insert("", tk.END, text="⚠️ 循环依赖警告 (Cycle Detected)", open=True)
            for t in layers[-1]:
                tid = t["id"]
                st_label = STATE_LABELS.get(t.get("state"), t.get("state"))
                tree.insert(cycle_node, tk.END, iid=tid, text=f"[{tid}] {t['title']}",
                            values=(st_label, "-", ", ".join(parents_map.get(tid, []))))

        def _on_double_click(event):
            selected = tree.selection()
            if selected and selected[0] in task_map:
                target_tid = selected[0]
                self._select_task(target_tid)
                win.destroy()

        tree.bind("<Double-1>", _on_double_click)

        bottom_bar = ttk.Frame(win, padding=12)
        bottom_bar.pack(fill=tk.X)
        ttk.Label(bottom_bar, text=f"共 {len(all_tasks)} 个任务 · {len(sorted_layer_keys)} 个并行波次", style="Muted.TLabel").pack(side=tk.LEFT)
        ttk.Button(bottom_bar, text="定位选中任务", style="Primary.TButton",
                   command=lambda: _on_double_click(None)).pack(side=tk.RIGHT)

    def _gui_ai_conflict_diagnosis(self):
        """Diagnose git merge conflict or validation failure using AI assistant."""
        task = self._selected_task()
        if not task:
            messagebox.showwarning("提示", "请先选择一个任务。")
            return
        tid = task["id"]

        existing = self._conflict_windows.get(tid)
        if existing and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return

        win = tk.Toplevel(self.root)
        self._conflict_windows[tid] = win
        win.title(T("conflict_window_title", self.lang))
        win.geometry("820x580")
        win.transient(self.root)
        win.bind("<Escape>", lambda e: win.destroy())

        top_f = ttk.Frame(win, padding=12)
        top_f.pack(fill=tk.X)
        header_label = ttk.Label(
            top_f, text="⚡ 正在诊断任务冲突与整改建议...",
            foreground="#0b5f93", font=("Microsoft YaHei UI", 11, "bold")
        )
        header_label.pack(anchor=tk.W)
        summary_label = ttk.Label(top_f, text="", style="Muted.TLabel")
        summary_label.pack(anchor=tk.W, pady=(2, 0))

        text = scrolledtext.ScrolledText(win, font=("Segoe UI", 10), relief=tk.FLAT)
        text.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
        text.insert(tk.END, "正在分析工作区代码冲突标记、验证失败原因和历史日志...\n")
        text.config(state=tk.DISABLED)

        def _worker():
            try:
                ws = self.coordinator.get_worktree(tid) if self.coordinator else None
                worktree_path = ws.get("worktree_path", "") if ws else ""
                conflict_files = []
                conflict_diff = ""

                if worktree_path and os.path.isdir(worktree_path):
                    try:
                        import subprocess
                        st_out = subprocess.check_output(
                            ["git", "status", "--porcelain"],
                            cwd=worktree_path, text=True, stderr=subprocess.STDOUT
                        )
                        for line in st_out.splitlines():
                            if line.startswith("UU ") or line.startswith("AA ") or line.startswith("UD ") or line.startswith("DU "):
                                conflict_files.append(line[3:].strip())
                        if conflict_files:
                            diff_out = subprocess.check_output(
                                ["git", "diff"],
                                cwd=worktree_path, text=True, stderr=subprocess.STDOUT
                            )
                            conflict_diff = diff_out
                    except Exception:
                        pass

                if conflict_files or task.get("state") == TaskState.CONFLICT.value:
                    diag = self.coordinator.diagnose_merge_conflict(
                        task_id=tid,
                        conflict_files=conflict_files or ["unknown_conflict"],
                        conflict_diff=conflict_diff
                    )
                    content = f"# ⚡ Git 合并冲突诊断报告 (任务 {tid})\n\n"
                    content += f"- **冲突文件数**: {diag.get('conflict_count', len(conflict_files))}\n"
                    content += f"- **冲突块数 (Hunks)**: {diag.get('conflict_hunks', 0)}\n"
                    content += f"- **推荐处置动作**: {diag.get('recommended_action', 'Rebase and resolve markers')}\n\n"
                    content += "## 冲突文件列表\n"
                    for cf in diag.get("conflict_files", []):
                        content += f"- `{cf}`\n"
                    content += "\n## 分步解决指南\n"
                    for res in diag.get("resolutions", []):
                        content += f"### 文件: `{res.get('file')}`\n"
                        content += f"- **策略**: {res.get('strategy')}\n"
                        content += f"- **说明**: {res.get('notes')}\n"
                        if res.get("auto_resolution"):
                            content += f"```\n{res.get('auto_resolution')}\n```\n"
                    headline = f"Git 冲突诊断：发现 {len(conflict_files)} 个冲突文件"
                    sub_text = diag.get("recommended_action", "")
                else:
                    diag_fix = self.coordinator.diagnose_task_failure(tid)
                    content = f"# 🔧 任务失败与整改诊断报告 (任务 {tid})\n\n"
                    content += f"- **根因归类**: `{diag_fix.failure_category}`\n"
                    content += f"- **诊断根因**: {diag_fix.root_cause or '无直接报错异常'}\n"
                    content += f"- **信心指数**: {int(diag_fix.confidence * 100)}%\n\n"
                    if diag_fix.suggested_files:
                        content += f"## 建议排查文件\n"
                        for sf in diag_fix.suggested_files:
                            content += f"- `{sf}`\n"
                        content += "\n"
                    content += f"## 整改指令 (Remediation Prompt)\n\n{diag_fix.remediation_prompt}\n\n"
                    if diag_fix.patch:
                        content += f"## 建议补丁 (Patch)\n```diff\n{diag_fix.patch}\n```\n"
                    headline = f"整改诊断：{diag_fix.failure_category}"
                    sub_text = diag_fix.root_cause[:80] if diag_fix.root_cause else "未发现明确报错"

                def _update_ui():
                    if not win.winfo_exists():
                        return
                    header_label.config(text=headline, foreground="#0b5f93")
                    summary_label.config(text=sub_text)
                    text.config(state=tk.NORMAL)
                    text.delete("1.0", tk.END)
                    text.insert(tk.END, content)
                    text.config(state=tk.DISABLED)

                self.root.after(0, _update_ui)
            except Exception as err:
                error_text = str(err)
                def _show_err():
                    if not win.winfo_exists():
                        return
                    header_label.config(text="诊断执行失败", foreground="#cf222e")
                    summary_label.config(text=error_text)
                    text.config(state=tk.NORMAL)
                    text.delete("1.0", tk.END)
                    text.insert(tk.END, f"诊断过程出现错误: {error_text}")
                    text.config(state=tk.DISABLED)
                self.root.after(0, _show_err)

        threading.Thread(target=_worker, daemon=True).start()

    def _merge_selected_task(self):

        task = self._selected_task()
        if not task:
            return
        if not messagebox.askyesno(
            "确认安全合并",
            "Bridge 将在隔离 integration worktree 中验证并合并提交，随后关闭 attempt、租约和工作区。\n\n"
            "此操作会更新目标分支。是否继续？",
            default=messagebox.NO,
        ):
            return
        tid = task["id"]
        try:
            entries = self.db.list_merge_entries(task_id=tid)
            entry_row = next(
                (entry for entry in reversed(entries) if entry.get("status") in ("queued", "merging", "merged")),
                None,
            )
            state = task["state"]
            if state == TaskState.APPROVED.value and entry_row is None:
                artifacts = self._load_artifacts(tid)
                commit = artifacts.get("submission_commit", "")
                entry = self.coordinator.enqueue_merge(tid, commit, confirmed=True)
                entry_row = {"id": entry.entry_id, "status": entry.status}
                self.coordinator.transition_task(
                    tid, TaskState.MERGE_QUEUED, actor="user", confirmed=True,
                )
                state = TaskState.MERGE_QUEUED.value
            elif state == TaskState.APPROVED.value and entry_row:
                self.coordinator.transition_task(
                    tid, TaskState.MERGE_QUEUED, actor="user", confirmed=True,
                )
                state = TaskState.MERGE_QUEUED.value

            entry_id = entry_row["id"] if entry_row else ""
            if not entry_id:
                raise CoordinatorError("没有可继续的合并条目。")

            entry = self.coordinator.get_merge_entry(entry_id)
            status = entry.status if entry else entry_row.get("status")
            if state == TaskState.MERGE_QUEUED.value and status == "queued":
                self.coordinator.start_merge(entry_id, confirmed=True)
                self.coordinator.transition_task(tid, TaskState.MERGING, actor="coordinator")
                state = TaskState.MERGING.value
                status = "merging"
            elif state == TaskState.MERGE_QUEUED.value and status in ("merging", "merged"):
                self.coordinator.transition_task(tid, TaskState.MERGING, actor="coordinator")
                state = TaskState.MERGING.value

            if state == TaskState.MERGING.value and status == "merging":
                self.coordinator.complete_merge(entry_id, "merged via Bridge UI", confirmed=True)
                status = "merged"
            if state == TaskState.MERGING.value and status == "merged":
                self.coordinator.transition_task(
                    tid, TaskState.DONE, actor="user", confirmed=True,
                )

            self._cleanup_completed_task(tid)
            self._log(f"任务 {tid} 已安全合并并完成。")
            self._refresh_coordinator()
            self._select_task(tid)
        except Exception as error:
            self._log(f"任务 {tid} 合并暂停：{error}")
            self._refresh_coordinator()
            self._select_task(tid)
            messagebox.showerror(
                "合并未完成",
                f"Bridge 已保留可恢复状态，没有静默跳过步骤。\n\n{error}",
            )

    def _handle_auto_receipt(self, task_id: str, result: dict):
        """Handle automatic receipt detection from the background watcher."""
        try:
            coordinator = self.coordinator
            if coordinator is None:
                return
            task = coordinator.get_task(task_id)
            if task and task.get("state") == TaskState.IN_PROGRESS.value:
                coordinator.transition_task(task_id, TaskState.SUBMITTED, actor="system")
                self._log(f"⚡ [自动感知] 任务 {task_id} 检测到 Agent 提交已就绪，已自动推进至 Submitted！")
                self._refresh_coordinator()
                if self.selected_task_id == task_id:
                    self._select_task(task_id)
        except Exception as e:
            self._log(f"自动推进提交状态提示: {e}")

    def _cleanup_completed_task(self, task_id: str):
        coordinator = self._require_coordinator()
        try:
            coordinator.stop_receipt_watcher()
        except Exception:
            pass
        attempt = coordinator.get_current_attempt(task_id)
        if attempt:
            coordinator.complete_attempt(attempt["id"], status="completed")
            self._revoke_attempt_lease(attempt, "task completed")
        if coordinator.get_worktree(task_id):
            try:
                coordinator.remove_worktree(task_id, force=False, confirmed=True)
            except Exception as error:
                self._log(f"任务已完成，但工作区需要手动检查：{error}")

    def _retry_selected_task(self):
        task = self._selected_task()
        if not task:
            return
        if not messagebox.askyesno(
            "开始新尝试",
            "Bridge 将关闭旧 attempt 和租约，并在确认工作区可清理后创建新尝试。是否继续？",
            default=messagebox.NO,
        ):
            return
        try:
            attempt = self.coordinator.get_current_attempt(task["id"])
            if self.coordinator.get_worktree(task["id"]):
                self.coordinator.remove_worktree(task["id"], force=True, confirmed=True)
            if attempt:
                self.coordinator.complete_attempt(attempt["id"], status="revision_required")
                self._revoke_attempt_lease(attempt, "new revision attempt")
            self.coordinator.transition_task(task["id"], TaskState.ASSIGNED, actor="user")
            self._refresh_coordinator()
            self._select_task(task["id"])
            self._gui_start_attempt(confirmed=True)
        except Exception as error:
            messagebox.showerror("无法开始新尝试", str(error))

    def _refresh_coordinator(self):
        """Refresh coordinator panel data."""
        if not self.coordinator or not self.current_project_id.get():
            return

        pid = self.current_project_id.get()
        try:
            summary = self.coordinator.get_project_summary(pid)
            self.summary_label.config(
                text=f"{summary.project_name}  ·  {summary.total_tasks} 个任务  ·  "
                     f"{summary.total_agents} 个 Agent  ·  {summary.pending_reviews} 个待审",
            )

            self.tasks = [t for t in self.coordinator.list_tasks() if t.get("project_id") == pid]
            selected = self.selected_task_id
            self.task_tree.delete(*self.task_tree.get_children())
            agent_names = {
                agent["id"]: agent["display_name"] for agent in self.coordinator.list_agents(pid)
            }
            for t in self.tasks:
                self.task_tree.insert("", tk.END, iid=t["id"], values=(
                    t["title"], STATE_LABELS.get(t["state"], t["state"]),
                    agent_names.get(t.get("owner_agent_id", ""), "-"),
                ))
            self.dynamic_agents = [
                {
                    "id": agent["id"], "name": agent["display_name"],
                    "role": ", ".join(json.loads(agent.get("roles_json", "[]") or "[]")),
                    "model": agent.get("model", ""),
                }
                for agent in self.coordinator.list_agents(pid)
            ]
            self._refresh_agent_list()
            self._default_agent_choices()
            if selected and self.task_tree.exists(selected):
                self._select_task(selected)
            elif self.tasks:
                self._select_task(self.tasks[0]["id"])
            else:
                self.selected_task_id = ""
                self.task_title_label.config(text="无选中的任务")
                self.task_meta_label.config(text="")
                self.task_goal_label.config(text="")
                self.action_title_label.config(text="")
                self.action_desc_label.config(text="请选择或创建一个任务")
                self.primary_action_button.config(text="无可用操作", state=tk.DISABLED)
                self.open_workspace_button.config(state=tk.DISABLED)
                self.copy_prompt_button.config(state=tk.DISABLED)
                self.view_diff_button.config(state=tk.DISABLED)
                self.ai_review_button.config(state=tk.DISABLED)
                self.conflict_ai_button.config(state=tk.DISABLED)
                self.task_dep_label.config(text="")

        except Exception as e:
            self._log(f"刷新失败: {e}")

    # ═══════════════════════════════════════════════════════════════
    # UI Helpers
    # ═══════════════════════════════════════════════════════════════

    def _log(self, msg: str):
        if hasattr(self, "preview_text"):
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.preview_text.insert(tk.END, f"[{timestamp}] {msg.rstrip()}\n\n")
            self.preview_text.see(tk.END)
        if hasattr(self, "status_label"):
            self.status_label.config(text=msg.split("\n")[0][:80], foreground="#0b5f93")

    def _refresh_agent_list(self):
        if not hasattr(self, "agent_tree"):
            return
        self.agent_tree.delete(*self.agent_tree.get_children())
        for agent in self.dynamic_agents:
            self.agent_tree.insert("", tk.END, iid=agent["id"], values=(
                agent["name"], agent.get("role", ""), agent.get("model", ""), "已启用",
            ))

    def _browse_dir(self):
        d = filedialog.askdirectory(title="选择目标项目文件夹")
        if d:
            self.target_dir.set(d)
            if not self.project_name.get():
                self.project_name.set(os.path.basename(d))

    def _on_mode_change(self, *args):
        mode = self.mode.get()
        if mode in TEMPLATES:
            tmpl = TEMPLATES[mode]
            self.agent_a_name.set(tmpl["agent_a"]["name"])
            self.agent_a_role.set(tmpl["agent_a"]["role"])
            self.agent_a_model.set(tmpl["agent_a"]["model"])
            self.agent_b_name.set(tmpl["agent_b"]["name"])
            self.agent_b_role.set(tmpl["agent_b"]["role"])
            self.agent_b_model.set(tmpl["agent_b"]["model"])

    def _toggle_agent_c(self):
        if self.agent_c_enabled.get():
            self.c_frame.pack_forget()
            self.c_toggle_btn.config(text="添加第三个执行 Agent")
            self.agent_c_enabled.set(False)
        else:
            self.c_frame.pack(fill=tk.X, pady=(12, 0), after=self.agent_optional_frame)
            self.c_toggle_btn.config(text="移除第三个执行 Agent")
            self.agent_c_enabled.set(True)

    def _switch_lang(self, lang):
        self.lang = lang
        set_lang(lang)
        self.root.title(T("window_title", lang))
        self._on_mode_change()
        self.status_label.config(text=T("status_ready", lang))

    def _init_custom_pipeline(self):
        self.custom_pipeline = [
            {"id": f"s{i}", "name": s["name"], "agent": s["agent"], "desc": s["desc"]}
            for i, s in enumerate(TEMPLATES["architect-engineer"]["pipeline"])
        ]
        self._refresh_pipeline_list()

    def _refresh_pipeline_list(self):
        self.pipeline_listbox.delete(0, tk.END)
        for stage in self.custom_pipeline:
            self.pipeline_listbox.insert(tk.END, f"{stage['name']}  [{stage['agent']}]")

    def _on_stage_select(self, event):
        sel = self.pipeline_listbox.curselection()
        if sel:
            idx = sel[0]
            stage = self.custom_pipeline[idx]
            self.stage_vars["stage_name"].set(stage["name"])
            self.stage_vars["stage_agent"].set(stage["agent"])
            self.stage_vars["stage_desc"].set(stage["desc"])

    def _add_stage(self):
        name = self.stage_vars["stage_name"].get().strip()
        if not name:
            messagebox.showwarning("提示", "请输入阶段名称")
            return
        self.custom_pipeline.append({
            "id": f"s{len(self.custom_pipeline)}",
            "name": name,
            "agent": self.stage_vars["stage_agent"].get().strip() or "Both",
            "desc": self.stage_vars["stage_desc"].get().strip()
        })
        self._refresh_pipeline_list()

    def _update_stage(self):
        sel = self.pipeline_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        self.custom_pipeline[idx]["name"] = self.stage_vars["stage_name"].get().strip()
        self.custom_pipeline[idx]["agent"] = self.stage_vars["stage_agent"].get().strip()
        self.custom_pipeline[idx]["desc"] = self.stage_vars["stage_desc"].get().strip()
        self._refresh_pipeline_list()

    def _delete_stage(self):
        sel = self.pipeline_listbox.curselection()
        if not sel:
            return
        del self.custom_pipeline[sel[0]]
        self._refresh_pipeline_list()

    def _move_stage_up(self):
        sel = self.pipeline_listbox.curselection()
        if not sel or sel[0] == 0:
            return
        idx = sel[0]
        self.custom_pipeline[idx], self.custom_pipeline[idx-1] = \
            self.custom_pipeline[idx-1], self.custom_pipeline[idx]
        self._refresh_pipeline_list()
        self.pipeline_listbox.selection_set(idx - 1)

    def _move_stage_down(self):
        sel = self.pipeline_listbox.curselection()
        if not sel or sel[0] >= len(self.custom_pipeline) - 1:
            return
        idx = sel[0]
        self.custom_pipeline[idx], self.custom_pipeline[idx+1] = \
            self.custom_pipeline[idx+1], self.custom_pipeline[idx]
        self._refresh_pipeline_list()
        self.pipeline_listbox.selection_set(idx + 1)

    def _get_agent_configs(self):
        agents = [
            {"name": self.agent_a_name.get(), "role": self.agent_a_role.get(), "model": self.agent_a_model.get()},
            {"name": self.agent_b_name.get(), "role": self.agent_b_role.get(), "model": self.agent_b_model.get()},
        ]
        if self.agent_c_enabled.get():
            agents.append({"name": self.agent_c_name.get(), "role": self.agent_c_role.get(), "model": self.agent_c_model.get()})
        return agents

    def _llm_analyze(self):
        if not self.llm_enabled.get():
            messagebox.showinfo("提示", "请先勾选「启用 LLM API」")
            return
        user_input = self.llm_input_text.get("1.0", tk.END).strip()
        if not user_input:
            messagebox.showinfo("提示", "请先输入项目需求描述")
            return
        self.llm_status.config(text="⏳ 分析中...", foreground="blue")
        self.root.update()
        api_key = self.llm_api_key.get().strip()
        api_base = self.llm_api_base.get().strip()
        model = self.llm_model.get().strip()
        mode_name = TEMPLATES[self.mode.get()]["name"]

        def task():
            try:
                result = llm_enhance_description(
                    api_key, api_base, model, user_input, mode_name,
                )
                self.root.after(0, lambda: self._apply_llm_result(result))
            except Exception as e:
                self.root.after(0, lambda msg=str(e)[:80]: self.llm_status.config(
                    text=f"❌ {msg}", foreground="red"))

        threading.Thread(target=task, daemon=True).start()

    def _apply_llm_result(self, result):
        try:
            if result.get("project_name"):
                self.project_name.set(result["project_name"])
            if result.get("agent_a_role"):
                self.agent_a_role.set(result["agent_a_role"])
            if result.get("agent_b_role"):
                self.agent_b_role.set(result["agent_b_role"])
            self.llm_status.config(text="✅ 分析完成，配置已自动填充", foreground="green")
        except Exception as e:
            self.llm_status.config(text=f"⚠️ 结果解析异常: {e}", foreground="orange")

    def _preview(self):
        mode = self.mode.get()
        agents = self._get_agent_configs()
        agent_a, agent_b = agents[0], agents[1]
        agent_c = agents[2] if len(agents) > 2 else None
        project_name = self.project_name.get() or "未命名项目"

        preview = f"""══════════════════════════════════════
  Bridge 生成预览
  模式: {TEMPLATES.get(mode, {}).get('name', 'Custom')}
  项目: {project_name}
  Agent A: {agent_a['name']} ({agent_a['role']})
  Agent B: {agent_b['name']} ({agent_b['role']}){"" if not agent_c else chr(10) + '  Agent C: ' + agent_c['name'] + ' (' + agent_c['role'] + ')'}
═══════════════════════════════════════
"""
        if self.coordinator:
            preview += f"\n  协调器: 已连接 (项目: {self.current_project_id.get()})\n"
            try:
                summary = self.coordinator.get_project_summary(self.current_project_id.get())
                preview += f"  任务数: {summary.total_tasks} | Agent 数: {summary.total_agents}\n"
            except Exception:
                pass

        self._log(preview)
        self.status_label.config(text="预览已更新", foreground="blue")

    def _generate(self):
        target = self.target_dir.get().strip()
        if not target:
            messagebox.showerror("错误", "请先选择目标项目文件夹")
            return
        if not os.path.isdir(target):
            messagebox.showerror("错误", f"文件夹不存在: {target}")
            return

        mode = self.mode.get()
        agents = self._get_agent_configs()
        agent_a, agent_b = agents[0], agents[1]
        agent_c = agents[2] if len(agents) > 2 else None
        a_name = agent_a['name']
        b_name = agent_b['name']
        project_name = self.project_name.get() or os.path.basename(target) or "未命名项目"

        bridge_dir = os.path.join(target, ".bridge")
        os.makedirs(bridge_dir, exist_ok=True)

        for agent_key, agent_dict in [("A", agent_a), ("B", agent_b)] + \
            ([("C", agent_c)] if agent_c else []):
            raw = agent_dict.get('name', '')
            safe = _sanitize_agent_name(raw)
            if safe != raw:
                if not messagebox.askyesno("名称修正",
                    f"Agent {agent_key} 名称 '{raw}' 包含不安全字符，已修正为 '{safe}'。\n继续？"):
                    return
                agent_dict['name'] = safe
        a_name = agent_a['name']
        b_name = agent_b['name']
        if agent_c:
            agent_c['name'] = _sanitize_agent_name(agent_c.get('name', 'agent-c'))

        def _safe_path(filename):
            full = os.path.realpath(os.path.join(bridge_dir, filename))
            if os.path.commonpath((full, os.path.realpath(bridge_dir))) != os.path.realpath(bridge_dir):
                raise ValueError(f"路径逃逸被阻止: {filename} -> {full}")
            return full

        if mode in ("parallel-team", "loop-engineering", "parallel-claim"):
            git_ok, git_msg = _check_git_repo(target)
            if not git_ok:
                if not messagebox.askyesno("Git 环境不完整", git_msg + "\n\n并行模式依赖 Git。\n是否继续？"):
                    return

        if mode == "custom":
            pipeline = self.custom_pipeline
            TEMPLATES["custom"] = {
                "name": "Custom", "pipeline": pipeline,
                "icon": "🛠️", "description": "自定义流水线"
            }
        else:
            pipeline = None

        check_files = ["AGENTS.md", "COLLAB.md", "README.md", "board.md",
                       f"agent-{a_name.lower()}.md", f"agent-{b_name.lower()}.md"]
        if agent_c:
            check_files.append(f"agent-{agent_c['name'].lower()}.md")
        existing = [f for f in check_files if os.path.exists(os.path.join(bridge_dir, f))]
        if os.path.exists(os.path.join(target, "BRIDGE.md")):
            existing.append("BRIDGE.md (项目根目录)")
        if existing:
            if not messagebox.askyesno("确认覆盖",
                                        f"以下文件已存在：\n" +
                                        "\n".join(f"  • {f}" for f in existing) +
                                        "\n\n是否继续？"):
                return

        try:
            all_files = {}

            if mode in ("parallel-team", "loop-engineering", "parallel-claim"):
                c_name = agent_c['name'] if agent_c else None
                all_files["AGENTS.md"] = generate_agents_md(mode, agent_a, agent_b, project_name, self.lang, agent_c=agent_c)
                all_files["README.md"] = generate_readme_md(mode, agent_a, agent_b, project_name, self.lang)
                all_files[f"agent-{a_name.lower()}.md"] = generate_agent_status_md(a_name, agent_a['role'], b_name, self.lang)
                all_files[f"agent-{b_name.lower()}.md"] = generate_agent_status_md(b_name, agent_b['role'], a_name, self.lang)
                all_files["board.md"] = generate_board_md(a_name, b_name, self.lang, agent_c_name=c_name)
                all_files["GIT_WORKTREE.md"] = generate_git_worktree_guide(self.lang)
                all_files["PARALLEL_GUIDE.md"] = generate_parallel_struct(a_name, b_name, c_name, self.lang)

                if agent_c:
                    c_name = agent_c['name']
                    all_files[f"agent-{c_name.lower()}.md"] = generate_agent_status_md(c_name, agent_c['role'], f"{a_name} / {b_name}", self.lang)

                tasks_dir = os.path.join(bridge_dir, "tasks")
                specs_dir = os.path.join(bridge_dir, "specs")
                os.makedirs(tasks_dir, exist_ok=True)
                os.makedirs(specs_dir, exist_ok=True)
                for name, content in all_files.items():
                    _atomic_write(_safe_path(name), content)
                _atomic_write(os.path.join(tasks_dir, "T001-example.md"),
                    f"# T001: 示例任务\n\n- **状态**：📌待认领\n- **OWNER**：无\n"
                    f"- **模块**：src/example\n\n## 目标\n[待填写]\n\n## 验收标准\n- [ ] 待填写\n")

                if mode == "loop-engineering":
                    all_files["loop-budget.md"] = generate_loop_budget_md(self.lang)
                    all_files["verify-template.md"] = generate_verify_template(self.lang)
                    _atomic_write(_safe_path("loop-budget.md"), all_files["loop-budget.md"])
                    _atomic_write(_safe_path("verify-template.md"), all_files["verify-template.md"])

                if mode == "parallel-claim":
                    all_files["spec-claim-guide.md"] = generate_spec_claim_template(self.lang)
                    _atomic_write(_safe_path("spec-claim-guide.md"), all_files["spec-claim-guide.md"])
                    _atomic_write(_safe_path(os.path.join("specs", "spec-example.md")),
                        "# Spec: 示例功能\nSpec claimed by agent: <unclaimed>\n\n## 目标\n[待填写]\n\n## 验收标准\n- [ ] 待填写\n")
            else:
                all_files["AGENTS.md"] = generate_agents_md(mode, agent_a, agent_b, project_name, self.lang, agent_c=agent_c)
                all_files["COLLAB.md"] = generate_collab_md(mode, agent_a, agent_b, pipeline, self.lang)
                all_files["README.md"] = generate_readme_md(mode, agent_a, agent_b, project_name, self.lang)

                specs_active = os.path.join(bridge_dir, "specs", "active")
                specs_review = os.path.join(specs_active, "review")
                specs_fix = os.path.join(specs_active, "fix-orders")
                specs_archive = os.path.join(bridge_dir, "specs", "archive")

                spec_files = {
                    os.path.join(specs_active, "tasks.md"): generate_tasks_md(mode, self.lang),
                    os.path.join(specs_active, "acceptance.md"): generate_acceptance_md(self.lang),
                    os.path.join(specs_active, "escalation.md"): T("tmpl.escalation_file", self.lang),
                    os.path.join(specs_review, "TEMPLATE.md"): generate_review_template(self.lang),
                    os.path.join(specs_fix, "TEMPLATE.md"): generate_fix_template(self.lang),
                }

                for d in [specs_active, specs_review, specs_fix, specs_archive]:
                    os.makedirs(d, exist_ok=True)

                for path, content in spec_files.items():
                    all_files[os.path.relpath(path, target)] = content
                    _atomic_write(path, content)

                for name in ["AGENTS.md", "COLLAB.md", "README.md"]:
                    _atomic_write(os.path.join(bridge_dir, name), all_files[name])

            gitignore_path = os.path.join(bridge_dir, ".gitignore")
            if not os.path.exists(gitignore_path):
                _atomic_write(gitignore_path, T("tmpl.gitignore_content", self.lang))

            bridge_entry = os.path.join(target, "BRIDGE.md")
            entry_content = f"""# BRIDGE.md — 入口指针

> ⚠️ 所有 AI Agent 协作文件位于 `.bridge/` 目录下。
> 请以 `.bridge/` 下的文件为准。

## 启动流程

1. 读 `.bridge/AGENTS.md` — 了解项目和流水线
2. 读 `.bridge/board.md`（并行模式）或 `.bridge/COLLAB.md`（串行模式）
3. 按流水线阶段开始工作

## 协调器

Bridge 协调器使用 SQLite 数据库管理任务状态、租约、审查和合并。
数据库位置: `.bridge/bridge.db`

生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}
"""
            _atomic_write(bridge_entry, entry_content)

            report = f"已在 {target} 中生成以下文件：\n\n  📄 BRIDGE.md (入口指针)\n"
            report += "\n".join(f"  ✅ {p}" for p in sorted(all_files.keys()))

            self._log(report)
            self.status_label.config(text=f"✅ 已生成 {len(all_files)} 个文件", foreground="green")
            messagebox.showinfo("生成完成", report)

        except Exception as e:
            messagebox.showerror("生成失败", str(e))
            self.status_label.config(text=f"❌ {str(e)[:60]}", foreground="red")


# ═══════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════

def main():
    root = tk.Tk()
    app = BridgeApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
