#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jp_course_editor.py — JSON GUI editor for Nihonjindes course source-of-truth.

Refactor highlights (vs *.old)
----------------------------
- Lessons collapsed by default; tree open/closed state is preserved across rebuilds.
- Contextual editor: the right pane shows only fields relevant to the current selection.
- Raw JSON is read-only, scrollable (both axes) and updates live; toggle Selected node / Full JSON.
  Raw view preserves scroll position during live updates.
- Undo/Redo live in the Edit menu (Ctrl+Z, Ctrl+Y, Ctrl+Shift+Z). Typing is coalesced into bursts.
- Toolbar buttons are contextual (shown only when applicable).
- Autosave (debounced, non-blocking) + automatic timestamped backup on open in ./backups.
- Safer save implementation (atomic replace, no fsync) to avoid UI freezes.

Usage
-----
    python3 jp_course_editor.py path/to/jp_course.json

Optional:
    python3 jp_course_editor.py path/to/jp_course.json --self-test
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import messagebox, ttk


DEFAULT_ITEM_IMAGE = "data/course-img/transparent.png"


# -----------------------------
# Model
# -----------------------------


@dataclass(frozen=True)
class NodeRef:
    kind: str  # 'course' | 'category' | 'lesson' | 'item'
    cat_i: Optional[int] = None
    lesson_i: Optional[int] = None
    item_i: Optional[int] = None


class CourseModel:
    """In-memory representation of the JSON plus helpers to navigate/mutate."""

    def __init__(self, data: Dict[str, Any]):
        self.data = data
        self._normalize_structure()

    def _normalize_structure(self) -> None:
        if not isinstance(self.data, dict):
            self.data = {}

        course = self.data.get("course")
        if not isinstance(course, dict):
            course = {}
            self.data["course"] = course

        cats = self.data.get("categories")
        if not isinstance(cats, list):
            cats = []
            self.data["categories"] = cats

        for ci, cat in enumerate(list(cats)):
            if not isinstance(cat, dict):
                cat = {}
                cats[ci] = cat
            cat.setdefault("name", "")
            cat.setdefault("slug", "")

            lessons = cat.get("lessons")
            if not isinstance(lessons, list):
                lessons = []
                cat["lessons"] = lessons

            for li, lesson in enumerate(list(lessons)):
                if not isinstance(lesson, dict):
                    lesson = {}
                    lessons[li] = lesson
                lesson.setdefault("name", "")
                lesson.setdefault("slug", "")
                # notes is optional

                items = lesson.get("items")
                if not isinstance(items, list):
                    items = []
                    lesson["items"] = items

                for ii, item in enumerate(list(items)):
                    if not isinstance(item, dict):
                        item = {}
                        items[ii] = item
                    item.setdefault("ja", "")
                    item.setdefault("it", "")
                    item.setdefault("image", DEFAULT_ITEM_IMAGE)

    def get_node(self, ref: NodeRef) -> Dict[str, Any]:
        if ref.kind == "course":
            return self.data["course"]
        if ref.kind == "category":
            return self.data["categories"][ref.cat_i]  # type: ignore[index]
        if ref.kind == "lesson":
            return self.data["categories"][ref.cat_i]["lessons"][ref.lesson_i]  # type: ignore[index]
        if ref.kind == "item":
            return self.data["categories"][ref.cat_i]["lessons"][ref.lesson_i]["items"][ref.item_i]  # type: ignore[index]
        raise ValueError(f"Unknown kind: {ref.kind}")

    # ---- mutation helpers ----

    def add_category(self, name: str = "New category", slug: str = "") -> NodeRef:
        cat = {"name": name, "slug": slug, "lessons": []}
        self.data["categories"].append(cat)
        return NodeRef("category", cat_i=len(self.data["categories"]) - 1)

    def add_lesson(self, cat_i: int, name: str = "New lesson", slug: str = "") -> NodeRef:
        lesson = {"name": name, "slug": slug, "items": []}
        self.data["categories"][cat_i]["lessons"].append(lesson)
        return NodeRef("lesson", cat_i=cat_i, lesson_i=len(self.data["categories"][cat_i]["lessons"]) - 1)

    def add_item(
        self,
        cat_i: int,
        lesson_i: int,
        ja: str = "",
        it: str = "",
        image: Optional[str] = None,
    ) -> NodeRef:
        img = (image or "").strip() or DEFAULT_ITEM_IMAGE
        item = {"ja": ja, "it": it, "image": img}
        self.data["categories"][cat_i]["lessons"][lesson_i]["items"].append(item)
        return NodeRef(
            "item",
            cat_i=cat_i,
            lesson_i=lesson_i,
            item_i=len(self.data["categories"][cat_i]["lessons"][lesson_i]["items"]) - 1,
        )

    def delete_node(self, ref: NodeRef) -> NodeRef:
        if ref.kind == "course":
            return ref
        if ref.kind == "category":
            del self.data["categories"][ref.cat_i]  # type: ignore[index]
            if self.data["categories"]:
                return NodeRef("category", cat_i=min(ref.cat_i or 0, len(self.data["categories"]) - 1))
            return NodeRef("course")
        if ref.kind == "lesson":
            lessons = self.data["categories"][ref.cat_i]["lessons"]  # type: ignore[index]
            del lessons[ref.lesson_i]  # type: ignore[index]
            if lessons:
                return NodeRef("lesson", cat_i=ref.cat_i, lesson_i=min(ref.lesson_i or 0, len(lessons) - 1))
            return NodeRef("category", cat_i=ref.cat_i)
        if ref.kind == "item":
            items = self.data["categories"][ref.cat_i]["lessons"][ref.lesson_i]["items"]  # type: ignore[index]
            del items[ref.item_i]  # type: ignore[index]
            if items:
                return NodeRef("item", cat_i=ref.cat_i, lesson_i=ref.lesson_i, item_i=min(ref.item_i or 0, len(items) - 1))
            return NodeRef("lesson", cat_i=ref.cat_i, lesson_i=ref.lesson_i)
        return NodeRef("course")

    def move_node(self, ref: NodeRef, direction: int) -> NodeRef:
        """direction: -1 up, +1 down"""
        if ref.kind == "category":
            arr = self.data["categories"]
            i = ref.cat_i or 0
            j = i + direction
            if 0 <= j < len(arr):
                arr[i], arr[j] = arr[j], arr[i]
                return NodeRef("category", cat_i=j)
            return ref
        if ref.kind == "lesson":
            arr = self.data["categories"][ref.cat_i]["lessons"]  # type: ignore[index]
            i = ref.lesson_i or 0
            j = i + direction
            if 0 <= j < len(arr):
                arr[i], arr[j] = arr[j], arr[i]
                return NodeRef("lesson", cat_i=ref.cat_i, lesson_i=j)
            return ref
        if ref.kind == "item":
            arr = self.data["categories"][ref.cat_i]["lessons"][ref.lesson_i]["items"]  # type: ignore[index]
            i = ref.item_i or 0
            j = i + direction
            if 0 <= j < len(arr):
                arr[i], arr[j] = arr[j], arr[i]
                return NodeRef("item", cat_i=ref.cat_i, lesson_i=ref.lesson_i, item_i=j)
            return ref
        return ref


# -----------------------------
# Utilities
# -----------------------------


def pretty_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False)


def atomic_write_text(path: Path, text: str) -> None:
    """Atomic write via os.replace; intentionally avoids fsync to reduce UI stalls."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
        os.replace(str(tmp_path), str(path))
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass


def timestamp_compact() -> str:
    # Local time
    return time.strftime("%Y%m%d-%H%M%S")


def safe_mkdir(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Defer to later error handling
        pass


# -----------------------------
# Self-test
# -----------------------------


def run_self_test(json_path: Path) -> int:
    print("[self-test] loading:", json_path, flush=True)
    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    model = CourseModel(data)

    _ = model.get_node(NodeRef("course"))
    if model.data.get("categories"):
        _ = model.get_node(NodeRef("category", cat_i=0))

    ref_cat = model.add_category()
    ref_lesson = model.add_lesson(ref_cat.cat_i or 0)
    ref_item = model.add_item(ref_lesson.cat_i or 0, ref_lesson.lesson_i or 0)
    item = model.get_node(ref_item)
    assert item.get("image"), "Item must have non-empty 'image' by default"

    _ = model.move_node(ref_item, -1)
    _ = model.delete_node(ref_item)

    tmp_out = json_path.parent / (json_path.stem + ".selftest.out.json")
    atomic_write_text(tmp_out, pretty_json(model.data) + "\n")
    assert tmp_out.exists(), "Atomic write failed"
    tmp_out.unlink(missing_ok=True)

    print("[self-test] OK", flush=True)
    return 0


# -----------------------------
# GUI
# -----------------------------


class EditorApp:
    def __init__(self, root: tk.Tk, json_path: Path, model: CourseModel):
        self.root = root
        self.json_path = json_path
        self.model = model

        # Version-based dirty tracking
        self.model_version = 0
        self.last_saved_version = 0

        # Undo/redo (snapshot of full JSON) – capped
        self.undo_stack: List[Dict[str, Any]] = []
        self.redo_stack: List[Dict[str, Any]] = []
        self.undo_limit = 100

        # Coalesced edit group (typing bursts)
        self._edit_group_active = False
        self._edit_group_after_id: Optional[str] = None
        self._edit_group_timeout_ms = 900

        # Autosave
        self._autosave_after_id: Optional[str] = None
        self._autosave_delay_ms = 900
        self._save_in_progress = False
        self._save_pending = False
        self._save_last_error: Optional[str] = None
        self._inflight_version: Optional[int] = None
        self._inflight_snapshot: Optional[Dict[str, Any]] = None

        # Tree state
        self.current_ref: NodeRef = NodeRef("course")
        self.current_key: Tuple[str, ...] = ("course",)
        self.node_open_state: Dict[Tuple[str, ...], bool] = {}
        self.id_to_ref: Dict[str, NodeRef] = {}
        self.id_to_key: Dict[str, Tuple[str, ...]] = {}
        self.key_to_id: Dict[Tuple[str, ...], str] = {}
        self._suspend_tree_events = False

        # Raw view
        self.raw_mode = tk.StringVar(value="selected")  # 'selected' | 'full'
        self._raw_update_after_id: Optional[str] = None
        self._raw_update_delay_ms = 150
        self._raw_scroll_pos: Dict[str, Tuple[float, float]] = {"selected": (0.0, 0.0), "full": (0.0, 0.0)}

        # Form change suppression
        self._suspend_form_events = False

        self._build_ui()
        self._bind_shortcuts()
        self._rebuild_tree(select_key=self.current_key)
        self._load_ref_into_form(self.current_ref)
        self._schedule_raw_update(force=True)
        self._refresh_toolbar()
        self._update_title_and_status()

    # ---------- keying / labels ----------

    def _slug_or_index(self, value: Any, idx: int) -> str:
        s = str(value or "").strip()
        return s if s else f"#{idx}"

    def _key_for_ref(self, ref: NodeRef) -> Tuple[str, ...]:
        if ref.kind == "course":
            return ("course",)
        if ref.kind == "category":
            cat = self.model.data["categories"][ref.cat_i]  # type: ignore[index]
            return ("category", self._slug_or_index(cat.get("slug"), ref.cat_i or 0))
        if ref.kind == "lesson":
            cat = self.model.data["categories"][ref.cat_i]  # type: ignore[index]
            c = self._slug_or_index(cat.get("slug"), ref.cat_i or 0)
            lesson = cat.get("lessons", [])[ref.lesson_i]  # type: ignore[index]
            l = self._slug_or_index(lesson.get("slug"), ref.lesson_i or 0)
            return ("lesson", c, l)
        if ref.kind == "item":
            cat = self.model.data["categories"][ref.cat_i]  # type: ignore[index]
            c = self._slug_or_index(cat.get("slug"), ref.cat_i or 0)
            lesson = cat.get("lessons", [])[ref.lesson_i]  # type: ignore[index]
            l = self._slug_or_index(lesson.get("slug"), ref.lesson_i or 0)
            return ("item", c, l, f"#{ref.item_i or 0}")
        return (ref.kind,)

    def _label_for_ref(self, ref: NodeRef) -> str:
        if ref.kind == "course":
            course = self.model.get_node(ref)
            title = course.get("title") or course.get("name") or "(untitled)"
            return f"Course: {title}"
        if ref.kind == "category":
            cat = self.model.get_node(ref)
            name = cat.get("name", "")
            return f"Category: {name}" if name else "Category"
        if ref.kind == "lesson":
            lesson = self.model.get_node(ref)
            name = lesson.get("name", "")
            return f"Lesson: {name}" if name else "Lesson"
        if ref.kind == "item":
            item = self.model.get_node(ref)
            ja = (item.get("ja") or "").strip()
            it = (item.get("it") or "").strip()
            if ja or it:
                return f"Item: {ja} → {it}".strip()
            return "Item"
        return ref.kind

    # ---------- UI ----------

    def _build_ui(self) -> None:
        self.root.title(f"JP Course Editor — {self.json_path.name}")
        self.root.geometry("1200x780")

        style = ttk.Style(self.root)
        try:
            style.configure("Treeview", rowheight=32)
        except tk.TclError:
            pass

        # Menu
        self._build_menu()

        # Layout
        self.paned = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        self.paned.pack(fill=tk.BOTH, expand=True)

        self.left = ttk.Frame(self.paned)
        self.right = ttk.Frame(self.paned)
        self.paned.add(self.left, weight=1)
        self.paned.add(self.right, weight=3)

        # Tree
        self.tree = ttk.Treeview(self.left, show="tree")
        self.tree.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        self.tree_scroll = ttk.Scrollbar(self.left, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree_scroll.pack(fill=tk.Y, side=tk.RIGHT)
        self.tree.configure(yscrollcommand=self.tree_scroll.set)

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<<TreeviewOpen>>", self._on_tree_open_close)
        self.tree.bind("<<TreeviewClose>>", self._on_tree_open_close)

        # Right header
        header = ttk.Frame(self.right)
        header.pack(fill=tk.X, padx=10, pady=(10, 6))
        self.sel_var = tk.StringVar(value="Selected: course")
        self.sel_label = ttk.Label(header, textvariable=self.sel_var)
        self.sel_label.pack(anchor="w")

        # Toolbar (contextual)
        self.toolbar = ttk.Frame(self.right)
        self.toolbar.pack(fill=tk.X, padx=10, pady=(0, 6))
        self._build_toolbar_buttons()

        # Editor frames (contextual)
        self.form_container = ttk.Frame(self.right)
        self.form_container.pack(fill=tk.X, padx=10)

        self._build_course_frame()
        self._build_catlesson_frame()
        self._build_item_frame()

        # Raw JSON panel
        raw_container = ttk.Labelframe(self.right, text="Raw JSON")
        raw_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        raw_controls = ttk.Frame(raw_container)
        raw_controls.pack(fill=tk.X, padx=6, pady=(6, 2))
        ttk.Label(raw_controls, text="View:").pack(side=tk.LEFT)
        ttk.Radiobutton(raw_controls, text="Selected node", value="selected", variable=self.raw_mode, command=self._on_raw_mode_changed).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Radiobutton(raw_controls, text="Full JSON", value="full", variable=self.raw_mode, command=self._on_raw_mode_changed).pack(
            side=tk.LEFT, padx=(6, 0)
        )

        # Text + scrollbars (both axes)
        raw_text_frame = ttk.Frame(raw_container)
        raw_text_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=(2, 6))
        self.raw_text = tk.Text(raw_text_frame, wrap=tk.NONE, height=12)
        self.raw_text.configure(state="disabled")
        ysb = ttk.Scrollbar(raw_text_frame, orient=tk.VERTICAL, command=self.raw_text.yview)
        xsb = ttk.Scrollbar(raw_text_frame, orient=tk.HORIZONTAL, command=self.raw_text.xview)
        self.raw_text.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.raw_text.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        raw_text_frame.rowconfigure(0, weight=1)
        raw_text_frame.columnconfigure(0, weight=1)

        # Read-only context menu
        self.raw_menu = tk.Menu(self.root, tearoff=0)
        self.raw_menu.add_command(label="Copy", command=lambda: self.raw_text.event_generate("<<Copy>>"))
        self.raw_menu.add_separator()
        self.raw_menu.add_command(label="Select all", command=lambda: self.raw_text.event_generate("<<SelectAll>>"))
        self.raw_text.bind("<Button-3>", self._show_raw_menu)

        # Status bar
        self.status_var = tk.StringVar(value="")
        status = ttk.Frame(self.root)
        status.pack(fill=tk.X, side=tk.BOTTOM)
        self.status_label = ttk.Label(status, textvariable=self.status_var, anchor="w")
        self.status_label.pack(fill=tk.X, padx=8, pady=4)

        self.root.protocol("WM_DELETE_WINDOW", self._request_close)

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Save now", accelerator="Ctrl+S", command=self._save_now)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._request_close)
        menubar.add_cascade(label="File", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=0)
        edit_menu.add_command(label="Undo", accelerator="Ctrl+Z", command=self._do_undo)
        edit_menu.add_command(label="Redo", accelerator="Ctrl+Y / Ctrl+Shift+Z", command=self._do_redo)
        menubar.add_cascade(label="Edit", menu=edit_menu)

        self.root.config(menu=menubar)

    def _bind_shortcuts(self) -> None:
        self.root.bind_all("<Control-s>", lambda _e: self._save_now())
        self.root.bind_all("<Control-z>", lambda _e: self._do_undo())
        self.root.bind_all("<Control-y>", lambda _e: self._do_redo())
        self.root.bind_all("<Control-Shift-Z>", lambda _e: self._do_redo())

    def _build_toolbar_buttons(self) -> None:
        # Create once, pack/unpack dynamically
        self.btn_add_category = ttk.Button(self.toolbar, text="+Category", command=self._add_category)
        self.btn_add_lesson = ttk.Button(self.toolbar, text="+Lesson", command=self._add_lesson)
        self.btn_add_item = ttk.Button(self.toolbar, text="+Item", command=self._add_item)
        self.btn_delete = ttk.Button(self.toolbar, text="Delete", command=self._delete_selected)
        self.btn_up = ttk.Button(self.toolbar, text="Up", command=lambda: self._move_selected(-1))
        self.btn_down = ttk.Button(self.toolbar, text="Down", command=lambda: self._move_selected(+1))

    def _pack_toolbar(self, widgets: List[ttk.Widget]) -> None:
        for w in self.toolbar.winfo_children():
            w.pack_forget()
        for w in widgets:
            w.pack(side=tk.LEFT, padx=4)

    def _build_course_frame(self) -> None:
        self.course_frame = ttk.Labelframe(self.form_container, text="Course")
        self.course_frame.columnconfigure(1, weight=1)

        self.course_title_var = tk.StringVar()
        self.course_slug_var = tk.StringVar()
        ttk.Label(self.course_frame, text="Title").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.course_title_entry = ttk.Entry(self.course_frame, textvariable=self.course_title_var, width=70)
        self.course_title_entry.grid(row=0, column=1, sticky="we", padx=6, pady=4)
        ttk.Label(self.course_frame, text="Slug").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.course_slug_entry = ttk.Entry(self.course_frame, textvariable=self.course_slug_var, width=70)
        self.course_slug_entry.grid(row=1, column=1, sticky="we", padx=6, pady=4)

        ttk.Label(self.course_frame, text="Description").grid(row=2, column=0, sticky="nw", padx=6, pady=4)
        self.course_desc_text = tk.Text(self.course_frame, height=4, wrap=tk.WORD)
        self.course_desc_text.grid(row=2, column=1, sticky="we", padx=6, pady=4)
        self.course_desc_text.bind("<<Modified>>", self._on_text_modified)

        self.course_title_var.trace_add("write", self._on_field_changed)
        self.course_slug_var.trace_add("write", self._on_field_changed)

    def _build_catlesson_frame(self) -> None:
        self.catlesson_frame = ttk.Labelframe(self.form_container, text="Category / Lesson")
        self.catlesson_frame.columnconfigure(1, weight=1)

        self.cl_name_var = tk.StringVar()
        self.cl_slug_var = tk.StringVar()
        ttk.Label(self.catlesson_frame, text="Name").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.cl_name_entry = ttk.Entry(self.catlesson_frame, textvariable=self.cl_name_var, width=70)
        self.cl_name_entry.grid(row=0, column=1, sticky="we", padx=6, pady=4)
        ttk.Label(self.catlesson_frame, text="Slug").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.cl_slug_entry = ttk.Entry(self.catlesson_frame, textvariable=self.cl_slug_var, width=70)
        self.cl_slug_entry.grid(row=1, column=1, sticky="we", padx=6, pady=4)

        ttk.Label(self.catlesson_frame, text="Notes").grid(row=2, column=0, sticky="nw", padx=6, pady=4)
        self.cl_notes_text = tk.Text(self.catlesson_frame, height=4, wrap=tk.WORD)
        self.cl_notes_text.grid(row=2, column=1, sticky="we", padx=6, pady=4)
        self.cl_notes_text.bind("<<Modified>>", self._on_text_modified)

        self.cl_name_var.trace_add("write", self._on_field_changed)
        self.cl_slug_var.trace_add("write", self._on_field_changed)

    def _build_item_frame(self) -> None:
        self.item_frame = ttk.Labelframe(self.form_container, text="Item")
        self.item_frame.columnconfigure(1, weight=1)

        self.item_ja_var = tk.StringVar()
        self.item_it_var = tk.StringVar()
        self.item_image_var = tk.StringVar()
        ttk.Label(self.item_frame, text="JA").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.item_ja_entry = ttk.Entry(self.item_frame, textvariable=self.item_ja_var, width=70)
        self.item_ja_entry.grid(row=0, column=1, sticky="we", padx=6, pady=4)
        ttk.Label(self.item_frame, text="IT").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.item_it_entry = ttk.Entry(self.item_frame, textvariable=self.item_it_var, width=70)
        self.item_it_entry.grid(row=1, column=1, sticky="we", padx=6, pady=4)
        ttk.Label(self.item_frame, text="Image").grid(row=2, column=0, sticky="w", padx=6, pady=4)
        self.item_image_entry = ttk.Entry(self.item_frame, textvariable=self.item_image_var, width=70)
        self.item_image_entry.grid(row=2, column=1, sticky="we", padx=6, pady=4)

        self.item_ja_var.trace_add("write", self._on_field_changed)
        self.item_it_var.trace_add("write", self._on_field_changed)
        self.item_image_var.trace_add("write", self._on_field_changed)

    # ---------- tree rebuild & state ----------

    def _rebuild_tree(self, select_key: Optional[Tuple[str, ...]] = None) -> None:
        self._suspend_tree_events = True
        try:
            self.tree.delete(*self.tree.get_children(""))
            self.id_to_ref.clear()
            self.id_to_key.clear()
            self.key_to_id.clear()

            course_ref = NodeRef("course")
            course_key = self._key_for_ref(course_ref)
            course_open = self.node_open_state.get(course_key, True)
            course_id = self.tree.insert("", "end", text=self._label_for_ref(course_ref), open=course_open)
            self._register_tree_node(course_id, course_ref)

            for ci, cat in enumerate(self.model.data.get("categories", [])):
                cat_ref = NodeRef("category", cat_i=ci)
                cat_key = self._key_for_ref(cat_ref)
                cat_open = self.node_open_state.get(cat_key, True)
                cat_id = self.tree.insert(course_id, "end", text=self._label_for_ref(cat_ref), open=cat_open)
                self._register_tree_node(cat_id, cat_ref)

                lessons = cat.get("lessons", []) if isinstance(cat, dict) else []
                for li, lesson in enumerate(lessons):
                    lesson_ref = NodeRef("lesson", cat_i=ci, lesson_i=li)
                    lesson_key = self._key_for_ref(lesson_ref)
                    # lessons: collapsed by default unless user opened it before
                    lesson_open = self.node_open_state.get(lesson_key, False)
                    lesson_id = self.tree.insert(cat_id, "end", text=self._label_for_ref(lesson_ref), open=lesson_open)
                    self._register_tree_node(lesson_id, lesson_ref)

                    items = lesson.get("items", []) if isinstance(lesson, dict) else []
                    for ii, _ in enumerate(items):
                        item_ref = NodeRef("item", cat_i=ci, lesson_i=li, item_i=ii)
                        item_id = self.tree.insert(lesson_id, "end", text=self._label_for_ref(item_ref), open=False)
                        self._register_tree_node(item_id, item_ref)

            # Apply selection
            if select_key is not None and select_key in self.key_to_id:
                iid = self.key_to_id[select_key]
                self.tree.selection_set(iid)
                self.tree.see(iid)
            else:
                # Select course root
                self.tree.selection_set(course_id)
                self.tree.see(course_id)
        finally:
            self._suspend_tree_events = False

    def _register_tree_node(self, iid: str, ref: NodeRef) -> None:
        key = self._key_for_ref(ref)
        self.id_to_ref[iid] = ref
        self.id_to_key[iid] = key
        self.key_to_id[key] = iid

    def _on_tree_open_close(self, _event: tk.Event) -> None:
        if self._suspend_tree_events:
            return
        iid = self.tree.focus()
        if not iid:
            return
        key = self.id_to_key.get(iid)
        if not key:
            return
        self.node_open_state[key] = bool(self.tree.item(iid, "open"))

    def _selected_ref_from_tree(self) -> Optional[NodeRef]:
        sel = self.tree.selection()
        if not sel:
            return None
        return self.id_to_ref.get(sel[0])

    # ---------- selection ----------

    def _on_tree_select(self, _event: tk.Event) -> None:
        if self._suspend_tree_events:
            return
        ref = self._selected_ref_from_tree()
        if ref is None:
            return
        self.current_ref = ref
        self.current_key = self._key_for_ref(ref)
        self.sel_var.set(f"Selected: {ref.kind} ({ref.cat_i},{ref.lesson_i},{ref.item_i})")
        self._load_ref_into_form(ref)
        self._schedule_raw_update(force=True)
        self._refresh_toolbar()

    # ---------- contextual form ----------

    def _hide_all_forms(self) -> None:
        for f in (self.course_frame, self.catlesson_frame, self.item_frame):
            f.pack_forget()

    def _load_ref_into_form(self, ref: NodeRef) -> None:
        node = self.model.get_node(ref)
        self._suspend_form_events = True
        try:
            self._hide_all_forms()
            if ref.kind == "course":
                self.course_frame.pack(fill=tk.X, pady=6)
                self.course_title_var.set(str(node.get("title", node.get("name", ""))))
                self.course_slug_var.set(str(node.get("slug", "")))
                desc = str(node.get("description", node.get("notes", "")) or "")
                self._set_text_widget(self.course_desc_text, desc)
            elif ref.kind in ("category", "lesson"):
                self.catlesson_frame.configure(text="Category" if ref.kind == "category" else "Lesson")
                self.catlesson_frame.pack(fill=tk.X, pady=6)
                self.cl_name_var.set(str(node.get("name", "")))
                self.cl_slug_var.set(str(node.get("slug", "")))
                notes = str(node.get("notes", "") or "")
                self._set_text_widget(self.cl_notes_text, notes)
            elif ref.kind == "item":
                self.item_frame.pack(fill=tk.X, pady=6)
                self.item_ja_var.set(str(node.get("ja", "")))
                self.item_it_var.set(str(node.get("it", "")))
                self.item_image_var.set(str(node.get("image", DEFAULT_ITEM_IMAGE)))
        finally:
            self._suspend_form_events = False

    def _set_text_widget(self, w: tk.Text, value: str) -> None:
        w.delete("1.0", tk.END)
        if value:
            w.insert(tk.END, value)
        w.edit_modified(False)

    def _get_text_widget(self, w: tk.Text) -> str:
        return w.get("1.0", tk.END).rstrip("\n")

    # ---------- modifications / undo coalescing ----------

    def _push_undo_snapshot(self) -> None:
        self.undo_stack.append(copy.deepcopy(self.model.data))
        if len(self.undo_stack) > self.undo_limit:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def _begin_coalesced_edit(self) -> None:
        if not self._edit_group_active:
            self._push_undo_snapshot()
            self._edit_group_active = True
        if self._edit_group_after_id is not None:
            try:
                self.root.after_cancel(self._edit_group_after_id)
            except Exception:
                pass
        self._edit_group_after_id = self.root.after(self._edit_group_timeout_ms, self._end_coalesced_edit)

    def _end_coalesced_edit(self) -> None:
        self._edit_group_active = False
        self._edit_group_after_id = None

    def _touch_model(self, structural: bool = False) -> None:
        """Mark model changed; schedule raw update + autosave; update title/status."""
        self.model._normalize_structure()
        self.model_version += 1
        if structural:
            self._end_coalesced_edit()
        self._schedule_raw_update()
        self._schedule_autosave()
        self._update_title_and_status()

    # ---------- form change handlers ----------

    def _on_field_changed(self, *_args: Any) -> None:
        if self._suspend_form_events:
            return
        self._begin_coalesced_edit()
        self._apply_form_to_model()
        self._touch_model(structural=False)
        self._update_tree_label_for_current()

    def _on_text_modified(self, event: tk.Event) -> None:
        if self._suspend_form_events:
            event.widget.edit_modified(False)
            return
        if not event.widget.edit_modified():
            return
        event.widget.edit_modified(False)
        self._begin_coalesced_edit()
        self._apply_form_to_model()
        self._touch_model(structural=False)
        self._update_tree_label_for_current()

    def _apply_form_to_model(self) -> None:
        ref = self.current_ref
        node = self.model.get_node(ref)
        if ref.kind == "course":
            node["title"] = self.course_title_var.get().strip()
            node["slug"] = self.course_slug_var.get().strip()
            desc = self._get_text_widget(self.course_desc_text).strip()
            if desc:
                node["description"] = desc
            else:
                node.pop("description", None)
        elif ref.kind in ("category", "lesson"):
            node["name"] = self.cl_name_var.get().strip()
            node["slug"] = self.cl_slug_var.get().strip()
            notes = self._get_text_widget(self.cl_notes_text).strip()
            if notes:
                node["notes"] = notes
            else:
                node.pop("notes", None)
        elif ref.kind == "item":
            node["ja"] = self.item_ja_var.get().strip()
            node["it"] = self.item_it_var.get().strip()
            img = self.item_image_var.get().strip() or DEFAULT_ITEM_IMAGE
            node["image"] = img
            if not self.item_image_var.get().strip():
                # keep UI in sync with stored default
                self._suspend_form_events = True
                try:
                    self.item_image_var.set(img)
                finally:
                    self._suspend_form_events = False

    # ---------- raw view ----------

    def _on_raw_mode_changed(self) -> None:
        # Save current scroll position for previous mode
        # (the callback fires after raw_mode has changed, so we read from both)
        self._schedule_raw_update(force=True)

    def _schedule_raw_update(self, force: bool = False) -> None:
        if self._raw_update_after_id is not None:
            try:
                self.root.after_cancel(self._raw_update_after_id)
            except Exception:
                pass
            self._raw_update_after_id = None
        delay = 0 if force else self._raw_update_delay_ms
        self._raw_update_after_id = self.root.after(delay, self._refresh_raw_view)

    def _refresh_raw_view(self) -> None:
        self._raw_update_after_id = None

        mode = self.raw_mode.get()
        # Preserve current scroll fraction before rewriting
        try:
            y0 = float(self.raw_text.yview()[0])
            x0 = float(self.raw_text.xview()[0])
            self._raw_scroll_pos[mode] = (y0, x0)
        except Exception:
            pass

        if mode == "full":
            obj = self.model.data
        else:
            obj = self.model.get_node(self.current_ref)
        text = pretty_json(obj) + "\n"

        # Preserve view; do NOT steal focus.
        y_target, x_target = self._raw_scroll_pos.get(mode, (0.0, 0.0))
        # Also preserve selection (best-effort)
        sel_ranges = None
        try:
            sel_ranges = self.raw_text.tag_ranges("sel")
        except Exception:
            sel_ranges = None
        insert_idx = None
        try:
            insert_idx = self.raw_text.index("insert")
        except Exception:
            insert_idx = None

        self.raw_text.configure(state="normal")
        try:
            self.raw_text.delete("1.0", tk.END)
            self.raw_text.insert("1.0", text)
        finally:
            self.raw_text.configure(state="disabled")

        try:
            self.raw_text.yview_moveto(y_target)
            self.raw_text.xview_moveto(x_target)
        except Exception:
            pass

        # Restore selection/insert if possible
        try:
            if insert_idx is not None:
                self.raw_text.mark_set("insert", insert_idx)
            if sel_ranges and len(sel_ranges) == 2:
                self.raw_text.tag_add("sel", sel_ranges[0], sel_ranges[1])
        except Exception:
            pass

    def _show_raw_menu(self, event: tk.Event) -> None:
        try:
            self.raw_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.raw_menu.grab_release()

    # ---------- toolbar visibility ----------

    def _can_move(self, ref: NodeRef, direction: int) -> bool:
        if ref.kind == "course":
            return False
        if ref.kind == "category":
            i = ref.cat_i or 0
            j = i + direction
            return 0 <= j < len(self.model.data.get("categories", []))
        if ref.kind == "lesson":
            lessons = self.model.data["categories"][ref.cat_i].get("lessons", [])  # type: ignore[index]
            i = ref.lesson_i or 0
            j = i + direction
            return 0 <= j < len(lessons)
        if ref.kind == "item":
            items = self.model.data["categories"][ref.cat_i]["lessons"][ref.lesson_i].get("items", [])  # type: ignore[index]
            i = ref.item_i or 0
            j = i + direction
            return 0 <= j < len(items)
        return False

    def _refresh_toolbar(self) -> None:
        ref = self.current_ref
        widgets: List[ttk.Widget] = [self.btn_add_category]

        if ref.kind in ("category", "lesson", "item"):
            widgets.append(self.btn_add_lesson)  # adding a lesson: needs a category context

        if ref.kind in ("lesson", "item"):
            widgets.append(self.btn_add_item)

        if ref.kind != "course":
            widgets.append(self.btn_delete)

        if self._can_move(ref, -1):
            widgets.append(self.btn_up)
        if self._can_move(ref, +1):
            widgets.append(self.btn_down)

        self._pack_toolbar(widgets)

    # ---------- structural commands ----------

    def _add_category(self) -> None:
        self._push_undo_snapshot()
        self.current_ref = self.model.add_category()
        self.current_key = self._key_for_ref(self.current_ref)
        # Ensure course open
        self.node_open_state[("course",)] = True
        self._touch_model(structural=True)
        self._rebuild_tree(select_key=self.current_key)
        self._load_ref_into_form(self.current_ref)
        self._refresh_toolbar()

    def _add_lesson(self) -> None:
        ref = self.current_ref
        cat_i: Optional[int] = None
        if ref.kind == "course":
            messagebox.showinfo("Select a category", "Select a category first (left tree).")
            return
        if ref.kind in ("category", "lesson", "item"):
            cat_i = ref.cat_i
        if cat_i is None:
            return

        self._push_undo_snapshot()
        self.current_ref = self.model.add_lesson(cat_i)
        self.current_key = self._key_for_ref(self.current_ref)

        # Ensure category open; lessons remain collapsed by default unless user opens.
        cat_ref = NodeRef("category", cat_i=cat_i)
        self.node_open_state[self._key_for_ref(cat_ref)] = True

        self._touch_model(structural=True)
        self._rebuild_tree(select_key=self.current_key)
        self._load_ref_into_form(self.current_ref)
        self._refresh_toolbar()

    def _add_item(self) -> None:
        ref = self.current_ref
        if ref.kind not in ("lesson", "item"):
            messagebox.showinfo("Select a lesson", "Select a lesson (or item) to add an item.")
            return
        cat_i, lesson_i = ref.cat_i, ref.lesson_i
        if cat_i is None or lesson_i is None:
            return

        self._push_undo_snapshot()
        self.current_ref = self.model.add_item(cat_i, lesson_i)
        self.current_key = self._key_for_ref(self.current_ref)

        # Ensure category+lesson open so the new item is visible.
        cat_ref = NodeRef("category", cat_i=cat_i)
        lesson_ref = NodeRef("lesson", cat_i=cat_i, lesson_i=lesson_i)
        self.node_open_state[self._key_for_ref(cat_ref)] = True
        self.node_open_state[self._key_for_ref(lesson_ref)] = True

        self._touch_model(structural=True)
        self._rebuild_tree(select_key=self.current_key)
        self._load_ref_into_form(self.current_ref)
        self._refresh_toolbar()

    def _delete_selected(self) -> None:
        ref = self.current_ref
        if ref.kind == "course":
            messagebox.showinfo("Not allowed", "You cannot delete the course root.")
            return
        if not messagebox.askyesno("Delete", "Delete the selected node?"):
            return

        self._push_undo_snapshot()
        new_ref = self.model.delete_node(ref)
        self.current_ref = new_ref
        self.current_key = self._key_for_ref(new_ref)
        self._touch_model(structural=True)
        self._rebuild_tree(select_key=self.current_key)
        self._load_ref_into_form(self.current_ref)
        self._refresh_toolbar()

    def _move_selected(self, direction: int) -> None:
        ref = self.current_ref
        if not self._can_move(ref, direction):
            return
        self._push_undo_snapshot()
        self.current_ref = self.model.move_node(ref, direction)
        self.current_key = self._key_for_ref(self.current_ref)
        self._touch_model(structural=True)
        self._rebuild_tree(select_key=self.current_key)
        self._load_ref_into_form(self.current_ref)
        self._refresh_toolbar()

    # ---------- tree label update (no rebuild) ----------

    def _update_tree_label_for_current(self) -> None:
        # If slug changed, key may change; easiest is to rebuild only when needed.
        # We'll do a light approach: update label for current iid if still resolvable,
        # and if key changed, rebuild once at the end of the typing burst.
        ref = self.current_ref
        old_key = self.current_key
        new_key = self._key_for_ref(ref)
        iid = self.key_to_id.get(old_key)
        if iid:
            try:
                self.tree.item(iid, text=self._label_for_ref(ref))
            except Exception:
                pass
        # If key changed (e.g. slug edited), rebuild at end of coalesced edit group.
        if new_key != old_key:
            self.current_key = new_key
            # Ensure we only rebuild once per burst.
            if self._edit_group_after_id is not None:
                # chain a rebuild right after the burst ends
                def _after_burst_rebuild() -> None:
                    # If still same selection
                    self._rebuild_tree(select_key=self.current_key)
                    self._refresh_toolbar()

                # Replace the end-of-burst callback while preserving logic.
                try:
                    self.root.after_cancel(self._edit_group_after_id)
                except Exception:
                    pass
                self._edit_group_after_id = self.root.after(self._edit_group_timeout_ms, lambda: (self._end_coalesced_edit(), _after_burst_rebuild()))

    # ---------- undo/redo ----------

    def _do_undo(self) -> None:
        if not self.undo_stack:
            return
        self._end_coalesced_edit()
        self.redo_stack.append(copy.deepcopy(self.model.data))
        self.model.data = self.undo_stack.pop()
        self.model._normalize_structure()
        self.model_version += 1
        self._schedule_autosave()
        self._rebuild_tree(select_key=self.current_key)
        ref = self._selected_ref_from_tree() or NodeRef("course")
        self.current_ref = ref
        self.current_key = self._key_for_ref(ref)
        self._load_ref_into_form(ref)
        self._schedule_raw_update(force=True)
        self._refresh_toolbar()
        self._update_title_and_status()

    def _do_redo(self) -> None:
        if not self.redo_stack:
            return
        self._end_coalesced_edit()
        self.undo_stack.append(copy.deepcopy(self.model.data))
        if len(self.undo_stack) > self.undo_limit:
            self.undo_stack.pop(0)
        self.model.data = self.redo_stack.pop()
        self.model._normalize_structure()
        self.model_version += 1
        self._schedule_autosave()
        self._rebuild_tree(select_key=self.current_key)
        ref = self._selected_ref_from_tree() or NodeRef("course")
        self.current_ref = ref
        self.current_key = self._key_for_ref(ref)
        self._load_ref_into_form(ref)
        self._schedule_raw_update(force=True)
        self._refresh_toolbar()
        self._update_title_and_status()

    # ---------- autosave ----------

    def _schedule_autosave(self) -> None:
        if self._autosave_after_id is not None:
            try:
                self.root.after_cancel(self._autosave_after_id)
            except Exception:
                pass
        self._autosave_after_id = self.root.after(self._autosave_delay_ms, self._autosave_fire)

    def _autosave_fire(self) -> None:
        self._autosave_after_id = None
        self._start_save_async(reason="autosave")

    def _save_now(self) -> None:
        # Immediate save request (still async)
        self._start_save_async(reason="manual")

    def _start_save_async(self, reason: str) -> None:
        # If already saving, queue another save.
        if self._save_in_progress:
            self._save_pending = True
            self._update_title_and_status(extra=f"Save queued ({reason})")
            return

        # Capture snapshot of data for the worker thread.
        version = self.model_version
        snapshot = copy.deepcopy(self.model.data)
        self._inflight_version = version
        self._inflight_snapshot = snapshot
        self._save_in_progress = True
        self._save_last_error = None
        self._update_title_and_status(extra="Saving…")

        def worker(path: Path, data_snapshot: Dict[str, Any]) -> None:
            ok = True
            err: Optional[str] = None
            try:
                text = pretty_json(data_snapshot) + "\n"
                atomic_write_text(path, text)
            except Exception as e:
                ok = False
                err = str(e)
            self.root.after(0, lambda: self._on_save_done(ok, err))

        t = threading.Thread(target=worker, args=(self.json_path, snapshot), daemon=True)
        t.start()

    def _on_save_done(self, ok: bool, err: Optional[str]) -> None:
        self._save_in_progress = False
        if ok:
            if self._inflight_version is not None:
                self.last_saved_version = self._inflight_version
            self._save_last_error = None
            self._update_title_and_status(extra=f"Saved {time.strftime('%H:%M:%S')}")
        else:
            self._save_last_error = err or "Unknown error"
            self._update_title_and_status(extra=f"Autosave failed: {self._save_last_error}")

        self._inflight_version = None
        self._inflight_snapshot = None

        if self._save_pending:
            self._save_pending = False
            # Save the latest state now.
            self._start_save_async(reason="queued")

    # ---------- title/status/dirty ----------

    def _is_dirty(self) -> bool:
        return self.model_version != self.last_saved_version or self._save_in_progress or self._save_pending

    def _update_title_and_status(self, extra: str = "") -> None:
        dirty_mark = "*" if self._is_dirty() else ""
        self.root.title(f"JP Course Editor{dirty_mark} — {self.json_path.name}")
        if self._save_in_progress:
            msg = "Saving…"
        elif self._save_last_error:
            msg = f"Autosave failed: {self._save_last_error}"
        elif self._is_dirty():
            msg = "Modified (autosave pending)"
        else:
            msg = f"Saved {time.strftime('%H:%M:%S')}"
        if extra:
            msg = extra if extra else msg
        self.status_var.set(msg)

    # ---------- close ----------

    def _request_close(self) -> None:
        # With autosave, we only warn if a save is pending/in progress or last save failed.
        if self._save_last_error:
            if not messagebox.askyesno(
                "Autosave failed",
                "Last autosave failed. Quit anyway?\n\n(You still have the startup backup in ./backups)",
            ):
                return
        if self._save_in_progress or self._save_pending or (self.model_version != self.last_saved_version):
            if not messagebox.askyesno(
                "Unsaved changes",
                "Autosave is pending or in progress. Quit anyway?\n\n(You still have the startup backup in ./backups)",
            ):
                return
        self.root.destroy()


def launch_gui(json_path: Path) -> int:
    # Load
    try:
        with json_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        # Tk not ready yet -> print
        print(f"ERROR: invalid JSON in {json_path}: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: cannot open {json_path}: {e}", file=sys.stderr)
        return 2

    # Backup on open
    backups_dir = json_path.parent / "backups"
    safe_mkdir(backups_dir)
    try:
        backup_name = f"{json_path.name}.bak-{timestamp_compact()}"
        shutil.copy2(str(json_path), str(backups_dir / backup_name))
    except Exception:
        # Non-fatal
        pass

    model = CourseModel(data)

    root = tk.Tk()
    app = EditorApp(root, json_path=json_path, model=model)
    root.mainloop()
    return 0


# -----------------------------
# CLI
# -----------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="GUI editor for jp_course.json")
    parser.add_argument("json_path", help="Path to jp_course.json")
    parser.add_argument("--self-test", action="store_true", help="Run non-GUI self tests and exit")
    args = parser.parse_args(argv)

    json_path = Path(args.json_path).expanduser().resolve()
    if not json_path.exists():
        print(f"ERROR: file not found: {json_path}", file=sys.stderr)
        return 2

    if args.self_test:
        return run_self_test(json_path)

    return launch_gui(json_path)


if __name__ == "__main__":
    raise SystemExit(main())
