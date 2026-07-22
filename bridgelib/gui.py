"""Bridge GUI 模块 — BridgeApp 类与入口。"""
import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from pathlib import Path
from datetime import datetime
import threading

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

# ═══════════════════════════════════════════════════════════════

class BridgeApp:
    def __init__(self, root):
        self.root = root
        self.lang = "zh"
        self.root.title(T("window_title", self.lang))
        self.root.geometry("1000x720")
        self.root.minsize(900, 600)

        # 样式
        style = ttk.Style()
        style.theme_use("clam")

        # 数据
        self.target_dir = tk.StringVar(value="")
        self.mode = tk.StringVar(value="architect-engineer")
        self.project_name = tk.StringVar(value="")
        self.agent_a_name = tk.StringVar(value="GPT")
        self.agent_a_role = tk.StringVar(value=T("mode.architect-engineer.agent_a.role", self.lang) if False else "架构师 / 审核员")
        self.agent_a_model = tk.StringVar(value="gpt-5.6-sol")
        self.agent_b_name = tk.StringVar(value="Reasonix")
        self.agent_b_role = tk.StringVar(value="工程师 / 执行者")
        self.agent_b_model = tk.StringVar(value="deepseek-v4-flash")

        # Agent C（可选）
        self.agent_c_enabled = tk.BooleanVar(value=False)
        self.agent_c_name = tk.StringVar(value="Agent C")
        self.agent_c_role = tk.StringVar(value="辅助执行者")
        self.agent_c_model = tk.StringVar(value="claude-haiku-4-5")

        # LLM 设置
        self.llm_enabled = tk.BooleanVar(value=False)
        self.llm_api_key = tk.StringVar(value="")
        self.llm_api_base = tk.StringVar(value="https://api.openai.com/v1")
        self.llm_model = tk.StringVar(value="gpt-5.6-luna")
        self.llm_user_input = tk.StringVar(value="")

        # 自定义流水线
        self.custom_pipeline = []

        self._build_ui()

    def _build_ui(self):
        # 主容器
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 顶部标题
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(title_frame, text="🌉 Bridge — AI Agent 协作桥接器",
                  font=("Microsoft YaHei", 16, "bold")).pack(side=tk.LEFT)
        ttk.Label(title_frame, text="在项目文件夹中生成 AI 协作流程文件",
                  font=("Microsoft YaHei", 9)).pack(side=tk.LEFT, padx=10)

        # 语言切换
        lang_frame = ttk.Frame(title_frame)
        lang_frame.pack(side=tk.RIGHT)
        self.btn_zh = ttk.Button(lang_frame, text="中", width=3,
                                  command=lambda: self._switch_lang("zh"))
        self.btn_zh.pack(side=tk.LEFT, padx=1)
        self.btn_en = ttk.Button(lang_frame, text="En", width=3,
                                  command=lambda: self._switch_lang("en"))
        self.btn_en.pack(side=tk.LEFT, padx=1)

        # Notebook 分页
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        # ─── Tab 1: 项目设置 ───
        tab1 = ttk.Frame(notebook, padding=15)
        notebook.add(tab1, text="  📁 项目设置  ")

        # 项目文件夹
        ttk.Label(tab1, text="目标项目文件夹", font=("", 10, "bold")).pack(anchor=tk.W, pady=(0, 5))
        dir_frame = ttk.Frame(tab1)
        dir_frame.pack(fill=tk.X, pady=(0, 15))
        ttk.Entry(dir_frame, textvariable=self.target_dir, width=60).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(dir_frame, text="浏览...", command=self._browse_dir).pack(side=tk.LEFT, padx=5)

        # 项目名称
        ttk.Label(tab1, text="项目名称", font=("", 10, "bold")).pack(anchor=tk.W, pady=(0, 5))
        ttk.Entry(tab1, textvariable=self.project_name, width=60).pack(fill=tk.X, pady=(0, 15))

        # 协作模式选择
        ttk.Label(tab1, text="协作模式", font=("", 10, "bold")).pack(anchor=tk.W, pady=(0, 10))

        mode_frame = ttk.Frame(tab1)
        mode_frame.pack(fill=tk.BOTH, expand=True)

        self.mode_frames = {}
        row = 0
        for mode_key, tmpl in TEMPLATES.items():
            concurrency = "⚡并行" if mode_key in ("parallel-team", "loop-engineering", "parallel-claim") else "🔗串行"
            fm = ttk.LabelFrame(mode_frame, text=f"{tmpl['icon']} {tmpl['name']} ({concurrency})")
            fm.grid(row=row, column=0, sticky="ew", pady=3, padx=(0, 10))
            mode_frame.columnconfigure(0, weight=1)

            desc_frame = ttk.Frame(fm, padding=8)
            desc_frame.pack(fill=tk.X)

            ttk.Radiobutton(
                desc_frame, text=tmpl['description'][:80] + "...",
                variable=self.mode, value=mode_key,
                command=self._on_mode_change
            ).pack(anchor=tk.W)

            ttk.Label(desc_frame, text=f"工序：{' → '.join([s['name'] for s in tmpl['pipeline']])}",
                      font=("", 8), foreground="gray").pack(anchor=tk.W, padx=20)

            self.mode_frames[mode_key] = fm
            row += 1

        # ─── Tab 2: Agent 配置 ───
        tab2 = ttk.Frame(notebook, padding=15)
        notebook.add(tab2, text="  🤖 Agent 配置  ")

        # Agent A
        a_frame = ttk.LabelFrame(tab2, text="Agent A（架构师/规划者）", padding=10)
        a_frame.pack(fill=tk.X, pady=(0, 15))
        for i, (label, var) in enumerate([
            ("名称", self.agent_a_name), ("角色描述", self.agent_a_role), ("模型", self.agent_a_model)
        ]):
            ttk.Label(a_frame, text=label, width=10).grid(row=i, column=0, sticky=tk.W, pady=3)
            ttk.Entry(a_frame, textvariable=var, width=40).grid(row=i, column=1, sticky=tk.EW, padx=5)
        a_frame.columnconfigure(1, weight=1)

        # Agent B
        b_frame = ttk.LabelFrame(tab2, text="Agent B（工程师/执行者）", padding=10)
        b_frame.pack(fill=tk.X, pady=(0, 15))
        for i, (label, var) in enumerate([
            ("名称", self.agent_b_name), ("角色描述", self.agent_b_role), ("模型", self.agent_b_model)
        ]):
            ttk.Label(b_frame, text=label, width=10).grid(row=i, column=0, sticky=tk.W, pady=3)
            ttk.Entry(b_frame, textvariable=var, width=40).grid(row=i, column=1, sticky=tk.EW, padx=5)
        b_frame.columnconfigure(1, weight=1)

        # Agent C（可选，默认折叠）
        self.c_frame = ttk.LabelFrame(tab2, text="Agent C（可选 — 第三 Agent）", padding=10)
        self.c_toggle_btn = ttk.Button(tab2, text="➕ 添加第三个 Agent",
                                        command=self._toggle_agent_c)
        self.c_toggle_btn.pack(fill=tk.X, pady=(0, 5))
        # 默认隐藏
        for i, (label, var) in enumerate([
            ("名称", self.agent_c_name), ("角色描述", self.agent_c_role), ("模型", self.agent_c_model)
        ]):
            ttk.Label(self.c_frame, text=label, width=10).grid(row=i, column=0, sticky=tk.W, pady=3)
            ttk.Entry(self.c_frame, textvariable=var, width=40).grid(row=i, column=1, sticky=tk.EW, padx=5)
        self.c_frame.columnconfigure(1, weight=1)

        # ─── Tab 3: LLM 辅助 ───
        tab3 = ttk.Frame(notebook, padding=15)
        notebook.add(tab3, text="  🧠 LLM 辅助  ")

        ttk.Checkbutton(tab3, text="启用 LLM API 辅助理解需求",
                        variable=self.llm_enabled).pack(anchor=tk.W, pady=(0, 10))

        llm_frame = ttk.LabelFrame(tab3, text="API 设置", padding=10)
        llm_frame.pack(fill=tk.X, pady=(0, 15))

        fields = [
            ("API Key", self.llm_api_key, True),
            ("API Base URL", self.llm_api_base, False),
            ("模型", self.llm_model, False),
        ]
        for i, (label, var, is_secret) in enumerate(fields):
            ttk.Label(llm_frame, text=label, width=12).grid(row=i, column=0, sticky=tk.W, pady=3)
            entry = ttk.Entry(llm_frame, textvariable=var, width=50,
                             show="*" if is_secret else "")
            entry.grid(row=i, column=1, sticky=tk.EW, padx=5)
            if is_secret:
                self._llm_key_entry = entry

        ttk.Label(tab3, text="描述你的项目需求（LLM 将辅助分析并自动填充配置）：",
                  font=("", 9)).pack(anchor=tk.W, pady=(10, 5))
        self.llm_input_text = scrolledtext.ScrolledText(tab3, height=4, width=60)
        self.llm_input_text.pack(fill=tk.X, pady=(0, 10))
        self.llm_input_text.insert("1.0", "")

        llm_btn_frame = ttk.Frame(tab3)
        llm_btn_frame.pack(fill=tk.X)
        ttk.Button(llm_btn_frame, text="🤖 AI 分析需求",
                   command=self._llm_analyze).pack(side=tk.LEFT, padx=(0, 10))
        self.llm_status = ttk.Label(llm_btn_frame, text="", foreground="gray")
        self.llm_status.pack(side=tk.LEFT)

        # ─── Tab 4: 自定义流水线 ───
        tab4 = ttk.Frame(notebook, padding=15)
        notebook.add(tab4, text="  🔧 流水线编辑  ")

        ttk.Label(tab4, text="仅「Custom」模式下生效。拖拽排序未实现，请用上下按钮调整。",
                  font=("", 9), foreground="gray").pack(anchor=tk.W, pady=(0, 10))

        pipeline_edit_frame = ttk.Frame(tab4)
        pipeline_edit_frame.pack(fill=tk.BOTH, expand=True)

        # 流水线列表
        list_frame = ttk.Frame(pipeline_edit_frame)
        list_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        ttk.Label(list_frame, text="流水线阶段", font=("", 9, "bold")).pack(anchor=tk.W)
        self.pipeline_listbox = tk.Listbox(list_frame, height=12, selectmode=tk.SINGLE)
        self.pipeline_listbox.pack(fill=tk.BOTH, expand=True, pady=5)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.pipeline_listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.pipeline_listbox.config(yscrollcommand=scrollbar.set)

        # 编辑区
        edit_frame = ttk.Frame(pipeline_edit_frame)
        edit_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        stage_fields = [
            ("阶段名称", "stage_name"),
            ("执行 Agent", "stage_agent"),
            ("描述", "stage_desc"),
        ]
        self.stage_vars = {}
        for i, (label, key) in enumerate(stage_fields):
            ttk.Label(edit_frame, text=label, font=("", 9)).pack(anchor=tk.W, pady=(5, 0))
            var = tk.StringVar()
            self.stage_vars[key] = var
            ttk.Entry(edit_frame, textvariable=var, width=30).pack(fill=tk.X)

        btn_row = ttk.Frame(edit_frame)
        btn_row.pack(fill=tk.X, pady=10)
        ttk.Button(btn_row, text="➕ 添加", command=self._add_stage).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="✏️ 更新", command=self._update_stage).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="🗑 删除", command=self._delete_stage).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="⬆", command=self._move_stage_up, width=3).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="⬇", command=self._move_stage_down, width=3).pack(side=tk.LEFT, padx=2)

        self.pipeline_listbox.bind("<<ListboxSelect>>", self._on_stage_select)

        # ─── 底部操作栏 ───
        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.pack(fill=tk.X, pady=(10, 0))

        # 预览区
        preview_frame = ttk.LabelFrame(main_frame, text="生成预览", padding=5)
        preview_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        self.preview_text = scrolledtext.ScrolledText(preview_frame, height=6, width=80,
                                                       font=("Consolas", 9))
        self.preview_text.pack(fill=tk.BOTH, expand=True)

        ttk.Button(bottom_frame, text="👁 预览生成内容",
                   command=self._preview).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(bottom_frame, text="🚀 生成到项目文件夹",
                   command=self._generate).pack(side=tk.LEFT)
        self.status_label = ttk.Label(bottom_frame, text="就绪", foreground="gray")
        self.status_label.pack(side=tk.RIGHT)

        # 初始化
        self._on_mode_change()
        self._init_custom_pipeline()

    def _browse_dir(self):
        d = filedialog.askdirectory(title="选择目标项目文件夹")
        if d:
            self.target_dir.set(d)
            # 自动提取项目名
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
            self.c_toggle_btn.config(text="➕ 添加第三个 Agent")
            self.agent_c_enabled.set(False)
        else:
            self.c_frame.pack(fill=tk.X, pady=(0, 15), before=self.c_toggle_btn)
            self.c_toggle_btn.config(text="➖ 移除第三个 Agent")
            self.agent_c_enabled.set(True)

    def _switch_lang(self, lang):
        self.lang = lang
        set_lang(lang)
        self.root.title(T("window_title", lang))
        # 刷新模式标签
        for mode_key, fm in self.mode_frames.items():
            concurrency = "⚡Parallel" if mode_key in ("parallel-team", "loop-engineering", "parallel-claim") else ("🔗Serial" if lang == "en" else "🔗串行")
            if lang == "zh":
                concurrency = "⚡并行" if mode_key in ("parallel-team", "loop-engineering", "parallel-claim") else "🔗串行"
            fm.configure(text=f"{TEMPLATES[mode_key]['icon']} {T(f'mode.{mode_key}.name', lang)} ({concurrency})")
        self._on_mode_change()
        self.status_label.config(text=T("status_ready", lang))

    def _init_custom_pipeline(self):
        """初始化自定义流水线（使用 architect-engineer 作为默认）"""
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
        agent = self.stage_vars["stage_agent"].get().strip()
        desc = self.stage_vars["stage_desc"].get().strip()
        if not name:
            messagebox.showwarning("提示", "请输入阶段名称")
            return
        self.custom_pipeline.append({
            "id": f"s{len(self.custom_pipeline)}",
            "name": name, "agent": agent or "Both", "desc": desc
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
        idx = sel[0]
        del self.custom_pipeline[idx]
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

        def task():
            try:
                api_key = self.llm_api_key.get().strip()
                api_base = self.llm_api_base.get().strip()
                model = self.llm_model.get().strip()
                mode_name = TEMPLATES[self.mode.get()]["name"]

                result = llm_enhance_description(api_key, api_base, model, user_input, mode_name)

                # 在主线程更新 UI
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
        a_name, b_name = agent_a['name'], agent_b['name']
        project_name = self.project_name.get() or "未命名项目"

        if mode == "custom":
            pipeline = self.custom_pipeline
            # 为 generate_agents_md / generate_collab_md 构造回退模板
            _fallback_tmpl = {
                "name": "Custom",
                "pipeline": pipeline,
                "icon": "🛠️",
                "description": "自定义流水线"
            }
            TEMPLATES["custom"] = _fallback_tmpl
        else:
            pipeline = None

        preview = f"""══════════════════════════════════════
  Bridge 生成预览
  模式: {TEMPLATES.get(mode, {}).get('name', 'Custom')}
  并发: {'⚡ 并行（分离文件+git仲裁）' if mode in ("parallel-team", "loop-engineering", "parallel-claim") else '🔗 串行（接力棒模式）'}
  项目: {project_name}
  Agent A: {agent_a['name']} ({agent_a['role']})
  Agent B: {agent_b['name']} ({agent_b['role']}){"" if not agent_c else chr(10) + '  Agent C: ' + agent_c['name'] + ' (' + agent_c['role'] + ')'}
══════════════════════════════════════
"""

        if mode in ("parallel-team", "loop-engineering", "parallel-claim"):
            preview += f"""
📄 AGENTS.md: 项目元信息 + {len(TEMPLATES[mode]['pipeline'])} 道工序流水线
📄 agent-{a_name.lower()}.md: {a_name} 独立状态（只有 {a_name} 写）
📄 agent-{b_name.lower()}.md: {b_name} 独立状态（只有 {b_name} 写）
📄 board.md: 共享任务看板（git 仲裁并发）
📄 PARALLEL_GUIDE.md: 并行协作快速入门
📄 GIT_WORKTREE.md: worktree 物理隔离指南
📂 tasks/: 独立任务文件
📂 specs/: 只读规范文档"""

            if mode == "loop-engineering":
                preview += """
📄 loop-budget.md: 循环预算追踪（rounds/tokens/time 守卫）
📄 verify-template.md: 独立 Verify agent 裁决模板"""
            elif mode == "parallel-claim":
                preview += """
📄 spec-claim-guide.md: Spec 认领机制（无 worktree 并行）"""

            preview += """

-- 关键设计 --
🔒 互斥写: 各自的状态文件互不冲突
📋 共享写: board.md 和 tasks/ 通过 git 控制并发
🔄 节奏: 原子操作写完立即 commit→push，开始前先 pull
"""
        else:
            preview += f"""
📄 AGENTS.md:
{generate_agents_md(mode, agent_a, agent_b, project_name, self.lang, agent_c=agent_c)[:600]}...

📄 COLLAB.md:
{generate_collab_md(mode, agent_a, agent_b, pipeline, self.lang)[:600]}...

📄 README.md:
{generate_readme_md(mode, agent_a, agent_b, project_name, self.lang)[:600]}...

... 以及 specs/ 目录下的 tasks.md、review 模板、fix-orders 模板、acceptance.md 等
"""
        self.preview_text.delete("1.0", tk.END)
        self.preview_text.insert("1.0", preview)
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
        a_name, b_name = agent_a['name'], agent_b['name']
        project_name = self.project_name.get() or os.path.basename(target) or "未命名项目"

        # ── P0 修复: 安全输出到 .bridge/ 子目录 ──
        bridge_dir = os.path.join(target, ".bridge")
        os.makedirs(bridge_dir, exist_ok=True)

        # ── P0 修复: Agent 名称消毒（防路径逃逸）──
        for agent_key, agent_dict in [("A", agent_a), ("B", agent_b)] + \
            ([("C", agent_c)] if agent_c else []):
            raw = agent_dict.get('name', '')
            safe = _sanitize_agent_name(raw)
            if safe != raw:
                if not messagebox.askyesno("名称修正",
                    f"Agent {agent_key} 名称 '{raw}' 包含不安全字符，已修正为 '{safe}'。\n继续？"):
                    return
                agent_dict['name'] = safe
        # 消毒后重新赋值——关键！后续代码用这些变量拼文件名
        a_name = agent_a['name']
        b_name = agent_b['name']
        if agent_c:
            agent_c['name'] = _sanitize_agent_name(agent_c.get('name', 'agent-c'))

        # ── 路径安全校验: 确保最终写入路径在 bridge_dir 内 ──
        def _safe_path(filename):
            full = os.path.realpath(os.path.join(bridge_dir, filename))
            if not full.startswith(os.path.realpath(bridge_dir) + os.sep) and full != os.path.realpath(bridge_dir):
                raise ValueError(f"路径逃逸被阻止: {filename} -> {full}")
            return full

        # ── P0 修复: 并行模式 Git 预检 ──
        if mode in ("parallel-team", "loop-engineering", "parallel-claim"):
            git_ok, git_msg = _check_git_repo(target)
            if not git_ok:
                if not messagebox.askyesno("Git 环境不完整", git_msg + "\n\n并行模式依赖 Git 进行并发控制。\n是否继续（仅生成文档，不保证并发安全）？"):
                    return

        # ── custom 模式：使用自定义流水线；需要为 TEMPLATES 查找提供回退 ──
        if mode == "custom":
            pipeline = self.custom_pipeline
            # 为 generate_agents_md / generate_collab_md 构造回退模板
            _fallback_tmpl = {
                "name": "Custom",
                "pipeline": pipeline,
                "icon": "🛠️",
                "description": "自定义流水线"
            }
            TEMPLATES["custom"] = _fallback_tmpl
        else:
            pipeline = None

        # 检查覆盖（.bridge/ 内文件 + 根目录 BRIDGE.md）
        check_files = ["AGENTS.md", "COLLAB.md", "README.md", "board.md",
                       f"agent-{a_name.lower()}.md", f"agent-{b_name.lower()}.md"]
        if agent_c:
            check_files.append(f"agent-{agent_c['name'].lower()}.md")
        existing = [f for f in check_files if os.path.exists(os.path.join(bridge_dir, f))]
        bridge_md_path = os.path.join(target, "BRIDGE.md")
        if os.path.exists(bridge_md_path):
            existing.append("BRIDGE.md (项目根目录)")
        if existing:
            if not messagebox.askyesno("确认覆盖",
                                        f"以下文件已存在，将被覆盖：\n" +
                                        "\n".join(f"  • {f}" for f in existing) +
                                        "\n\n是否继续？"):
                return

        try:
            all_files = {}

            if mode in ("parallel-team", "loop-engineering", "parallel-claim"):
                # ── 并行模式：独立文件结构 ──
                c_name = agent_c['name'] if agent_c else None
                all_files["AGENTS.md"] = generate_agents_md(mode, agent_a, agent_b, project_name, self.lang, agent_c=agent_c)
                all_files["README.md"] = generate_readme_md(mode, agent_a, agent_b, project_name, self.lang)
                all_files[f"agent-{a_name.lower()}.md"] = generate_agent_status_md(
                    a_name, agent_a['role'], b_name, self.lang)
                all_files[f"agent-{b_name.lower()}.md"] = generate_agent_status_md(
                    b_name, agent_b['role'], a_name, self.lang)
                all_files["board.md"] = generate_board_md(a_name, b_name, self.lang, agent_c_name=c_name)
                all_files["GIT_WORKTREE.md"] = generate_git_worktree_guide(self.lang)
                all_files["PARALLEL_GUIDE.md"] = generate_parallel_struct(a_name, b_name, c_name, self.lang)

                # Agent C（如果启用）
                if agent_c:
                    c_name = agent_c['name']
                    all_files[f"agent-{c_name.lower()}.md"] = generate_agent_status_md(
                        c_name, agent_c['role'], f"{a_name} / {b_name}", self.lang)

                tasks_dir = os.path.join(bridge_dir, "tasks")
                specs_dir = os.path.join(bridge_dir, "specs")
                os.makedirs(tasks_dir, exist_ok=True)
                os.makedirs(specs_dir, exist_ok=True)
                # 写入文件
                for name, content in all_files.items():
                    _atomic_write(_safe_path(name), content)
                # 创建示例任务文件（原子写入）
                _atomic_write(os.path.join(tasks_dir, "T001-example.md"),
                    f"# T001: 示例任务\n\n- **状态**：📌待认领\n- **OWNER**：无\n"
                    f"- **模块**：src/example\n\n## 目标\n[待填写]\n\n## 验收标准\n- [ ] 待填写\n")

                # ── Loop-Engineering 额外文件 ──
                if mode == "loop-engineering":
                    all_files["loop-budget.md"] = generate_loop_budget_md(self.lang)
                    all_files["verify-template.md"] = generate_verify_template(self.lang)
                    _atomic_write(_safe_path("loop-budget.md"), all_files["loop-budget.md"])
                    _atomic_write(_safe_path("verify-template.md"), all_files["verify-template.md"])

                # ── Parallel-Claim 额外文件 ──
                if mode == "parallel-claim":
                    all_files["spec-claim-guide.md"] = generate_spec_claim_template(self.lang)
                    _atomic_write(_safe_path("spec-claim-guide.md"), all_files["spec-claim-guide.md"])
                    # 创建示例 spec 文件（原子写入）
                    _atomic_write(_safe_path(os.path.join("specs", "spec-example.md")),
                        "# Spec: 示例功能\nSpec claimed by agent: <unclaimed>\n\n## 目标\n[待填写]\n\n## 验收标准\n- [ ] 待填写\n")
            else:
                # ── 串行模式：原逻辑 ──
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
                    os.path.join(specs_active, "escalation.md"):
                        T("tmpl.escalation_file", self.lang),
                    os.path.join(specs_review, "TEMPLATE.md"): generate_review_template(self.lang),
                    os.path.join(specs_fix, "TEMPLATE.md"): generate_fix_template(self.lang),
                }

                for d in [specs_active, specs_review, specs_fix, specs_archive]:
                    os.makedirs(d, exist_ok=True)

                for path, content in spec_files.items():
                    all_files[os.path.relpath(path, target)] = content
                    _atomic_write(path, content)

                # 写入根文件（原子写入）
                for name in ["AGENTS.md", "COLLAB.md", "README.md"]:
                    _atomic_write(os.path.join(bridge_dir, name), all_files[name])

            # .gitignore（原子写入）
            gitignore_path = os.path.join(bridge_dir, ".gitignore")
            if not os.path.exists(gitignore_path):
                _atomic_write(gitignore_path, T("tmpl.gitignore_content", self.lang))

            # 生成报告
            # ── 项目根入口指针: 告诉 Agent 所有协作文件在 .bridge/ 下 ──
            bridge_entry = os.path.join(target, "BRIDGE.md")
            entry_content = f"""# BRIDGE.md — 入口指针

> ⚠️ 所有 AI Agent 协作文件位于 `.bridge/` 目录下，不在此文件所在目录。
> 请以 `.bridge/` 下的文件为准。

## 启动流程

1. 读 `.bridge/AGENTS.md` — 了解项目和流水线
2. 读 `.bridge/board.md`（并行模式）或 `.bridge/COLLAB.md`（串行模式）— 了解当前状态
3. 按流水线阶段开始工作

## 文件索引

所有协作文件在 `.bridge/` 目录中。
生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}
"""
            _atomic_write(bridge_entry, entry_content)

            report = f"已在 {target} 中生成以下文件：\n\n  📄 BRIDGE.md (入口指针 — Agent 应从此文件开始)\n"
            report += "\n".join(f"  ✅ {p}" for p in sorted(all_files.keys()))

            self.preview_text.delete("1.0", tk.END)
            self.preview_text.insert("1.0", report)
            self.status_label.config(text=f"✅ 已生成 {len(all_files)} 个文件", foreground="green")
            messagebox.showinfo("生成完成", report)

        except Exception as e:
            messagebox.showerror("生成失败", str(e))
            self.status_label.config(text=f"❌ {str(e)[:60]}", foreground="red")


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

def main():
    root = tk.Tk()
    app = BridgeApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
