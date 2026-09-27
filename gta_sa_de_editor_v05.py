"""
GTA San Andreas DE Save Editor - standalone v0.4 preview

Standard library only: Python with Tkinter.

Save editing:
- Preserves the supplied editor's money and checksum implementation.
- Uses the user-tested, version-41 POOLS player-layout signature.
- Does not implement general block parsing, zlib rebuilding, or conversion.
- Treats the old named-block scanner as a heuristic, not a format specification.

Collectibles:
- Manual checklist and schematic X/Y coordinate plot.
- No bundled location dataset.
- No reading or writing of collectible flags in game saves.
- Workspace progress is stored separately as JSON.
"""

import csv
import ctypes
import hashlib
import json
import math
import os
import struct
import tkinter as tk

from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


APP_TITLE = "GTA SA DE Save Editor — Standalone v0.4 preview"
MAGIC = b"\x00\xff\x00\xff"
MAX_MONEY = 0x7FFFFFFF

POOLS_MARKER = struct.pack("<I", 6) + b"POOLS\x00"
SUPPORTED_VERSION = 41
POOLS_PREFIX = struct.pack("<I", 1)
PLAYER_PREFIX = struct.pack("<IIII", 1, 0, 0, 0x1DC)
FIELD_OFFSETS = {"health": 0x20, "armor": 0x24}


def u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def f32(data, offset):
    return struct.unpack_from("<f", data, offset)[0]


def checksum(data):
    temporary = bytearray(data)
    temporary[:24] = b"\0" * 24
    return bytes(value ^ 255 for value in hashlib.md5(temporary).digest())


def header_info(data):
    if len(data) < 0x28 or data[:4] != MAGIC:
        raise ValueError("Not a recognized GTA SA DE save.")
    return u32(data, 4), bytes(data[8:24]) == checksum(data)


def update_checksum(data):
    data[8:24] = checksum(data)


def parse_blocks(data):
    """Preserved legacy scanner. Reported sizes are only interpretations."""
    blocks = []
    seen = set()
    position = 0x24
    while position + 9 <= len(data):
        length = u32(data, position)
        if 2 <= length <= 64 and position + 8 + length <= len(data):
            raw = data[position + 4:position + 4 + length]
            if raw.endswith(b"\0"):
                try:
                    name = raw[:-1].decode("ascii")
                except UnicodeDecodeError:
                    name = ""
                if name and all(32 <= value < 127 for value in raw[:-1]):
                    size_offset = position + 4 + length
                    size = u32(data, size_offset)
                    data_offset = size_offset + 4
                    if data_offset + size <= len(data) and name not in seen:
                        blocks.append((name, position, data_offset, size))
                        seen.add(name)
                        position = data_offset + size
                        continue
        position += 1
    return blocks


def find_block(data, name):
    return next(
        (block for block in parse_blocks(data) if block[0] == name),
        None,
    )


def money_location(data):
    block = find_block(data, "PLAYERINFO")
    if block is None or block[3] < 16:
        raise ValueError("PLAYERINFO is unavailable or too small.")
    return block[2]


def set_money(data, value):
    if not 0 <= value <= MAX_MONEY:
        raise ValueError(f"Money must be between 0 and {MAX_MONEY:,}.")
    offset = money_location(data)
    struct.pack_into("<I", data, offset, value)
    struct.pack_into("<I", data, offset + 0x0C, value)


def locate_player(data):
    """Recognize the tested sample signature; never search for float values."""
    version, _ = header_info(data)
    if version != SUPPORTED_VERSION:
        raise ValueError(f"Player layout not enabled for version {version}.")

    marker = data.find(POOLS_MARKER)
    if marker < 0:
        raise ValueError("Expected POOLS marker not found.")
    if data.find(POOLS_MARKER, marker + 1) >= 0:
        raise ValueError("Multiple POOLS markers; player layout is ambiguous.")

    after_name = marker + len(POOLS_MARKER)
    base = after_name + len(POOLS_PREFIX)
    if base + 0x28 > len(data):
        raise ValueError("Candidate player record is truncated.")
    if bytes(data[after_name:base]) != POOLS_PREFIX:
        raise ValueError("POOLS prefix differs from the tested layout.")
    if bytes(data[base:base + 16]) != PLAYER_PREFIX:
        raise ValueError("Player prefix differs from the tested layout.")

    coordinates = struct.unpack_from("<fff", data, base + 0x10)
    values = [f32(data, base + offset) for offset in FIELD_OFFSETS.values()]
    if not all(math.isfinite(value) for value in (*coordinates, *values)):
        raise ValueError("Player record contains non-finite values.")
    return base


def encode_player_float(text, label):
    try:
        value = float(text.strip())
    except ValueError:
        raise ValueError(f"{label} must be a number.")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be finite and non-negative.")
    try:
        packed = struct.pack("<f", value)
    except (OverflowError, struct.error):
        raise ValueError(f"{label} is too large for a 32-bit float.")
    if not math.isfinite(struct.unpack("<f", packed)[0]):
        raise ValueError(f"{label} cannot be stored as a finite float.")
    return packed


def get_last_mission(data):
    length = u32(data, 0x24)
    if not 1 <= length <= 128 or 0x28 + length > len(data):
        return ""
    return bytes(data[0x28:0x28 + length]).split(b"\0", 1)[0].decode(
        "ascii", "replace"
    )


def set_last_mission(data, text):
    if "\0" in text:
        raise ValueError("Mission key cannot contain a null character.")
    try:
        encoded = text.encode("ascii") + b"\0"
    except UnicodeEncodeError:
        raise ValueError("Mission key must contain ASCII characters only.")
    length = u32(data, 0x24)
    if not 1 <= length <= 128 or 0x28 + length > len(data):
        raise ValueError("Mission-key layout is unavailable.")
    if len(encoded) != length:
        raise ValueError("Mission key must keep the original byte length.")
    data[0x28:0x28 + length] = encoded


def documents_folder():
    if os.name == "nt":
        try:
            from ctypes import wintypes
            function = ctypes.windll.shell32.SHGetFolderPathW
            function.argtypes = [
                wintypes.HWND, ctypes.c_int, wintypes.HANDLE,
                wintypes.DWORD, wintypes.LPWSTR,
            ]
            function.restype = ctypes.c_long
            buffer = ctypes.create_unicode_buffer(32768)
            if function(None, 5, None, 0, buffer) == 0 and buffer.value:
                return Path(buffer.value)
        except (AttributeError, OSError, ValueError):
            pass
    return Path.home() / "Documents"


def default_save_folder(current_path=None):
    if current_path is not None and Path(current_path).parent.is_dir():
        return Path(current_path).parent

    documents = documents_folder()
    game = documents / "Rockstar Games" / "GTA San Andreas Definitive Edition"
    profiles = game / "Profiles"

    if profiles.is_dir():
        try:
            folders = sorted(
                (path for path in profiles.iterdir() if path.is_dir()),
                key=lambda path: path.name.lower(),
            )
        except OSError:
            return profiles

        latest = None
        for folder in folders:
            try:
                for path in folder.iterdir():
                    if path.suffix.lower() != ".sav":
                        continue
                    try:
                        if not path.is_file():
                            continue
                        modified = path.stat().st_mtime_ns
                    except OSError:
                        continue
                    if latest is None or modified > latest[0]:
                        latest = (modified, folder)
            except OSError:
                continue

        if latest is not None:
            return latest[1]
        return folders[0] if len(folders) == 1 else profiles

    for folder in (game, documents, Path.home(), Path.cwd()):
        if folder.is_dir():
            return folder
    return Path.cwd()


@dataclass
class PlayerState:
    money: int
    money_mirror: int
    health: object = None
    armor: object = None
    position: object = None


class SaveDocument:
    """Preserve original bytes; patch only explicitly changed fields."""

    def __init__(self, path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        self.version, self.valid_checksum = header_info(self.data)
        self.blocks = parse_blocks(self.data)

        offset = money_location(self.data)
        self.state = PlayerState(
            money=u32(self.data, offset),
            money_mirror=u32(self.data, offset + 0x0C),
        )
        self.player_base = None
        self.player_error = ""
        try:
            self.player_base = locate_player(self.data)
            self.state.health = f32(self.data, self.player_base + 0x20)
            self.state.armor = f32(self.data, self.player_base + 0x24)
            self.state.position = struct.unpack_from(
                "<fff", self.data, self.player_base + 0x10
            )
        except ValueError as error:
            self.player_error = str(error)

        self.display = {
            "money": str(self.state.money),
            "health": (
                format(self.state.health, ".9g")
                if self.state.health is not None else ""
            ),
            "armor": (
                format(self.state.armor, ".9g")
                if self.state.armor is not None else ""
            ),
            "mission": get_last_mission(self.data),
        }

    def build_output(self, values):
        out = bytearray(self.data)
        edits = {}

        if values["money"].strip() != self.display["money"]:
            try:
                money = int(values["money"].replace(",", "").strip())
            except ValueError:
                raise ValueError("Money must be a whole number.")
            set_money(out, money)
            edits["money"] = money

        for field in FIELD_OFFSETS:
            if values[field].strip() == self.display[field]:
                continue
            if self.player_base is None:
                raise ValueError("Health/armor layout is unavailable.")
            packed = encode_player_float(values[field], field.title())
            offset = self.player_base + FIELD_OFFSETS[field]
            out[offset:offset + 4] = packed
            edits[field] = struct.unpack("<f", packed)[0]

        if values["mission"] != self.display["mission"]:
            set_last_mission(out, values["mission"])
            edits["mission"] = values["mission"]

        if len(out) != len(self.data):
            raise ValueError("Unexpected file-size change.")
        update_checksum(out)
        if not header_info(out)[1]:
            raise ValueError("Output checksum self-check failed.")
        return bytes(out), edits


class CollectiblesTab(ttk.Frame):
    """Manual workspace only. Never receives or modifies a SaveDocument."""

    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self.items = []
        self.dirty = False
        self.category = tk.StringVar(value="All")
        self.points = []
        self.summary = tk.StringVar()

        ttk.Label(
            self,
            text=(
                "Manual checklist + coordinate plot. No bundled locations; "
                "NOT linked to collection flags in your save."
            ),
            wraplength=950,
        ).pack(anchor="w", pady=(0, 8))

        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x")
        for label, command in [
            ("Import locations CSV", self.import_csv),
            ("Open checklist JSON", self.open_json),
            ("Save checklist JSON", self.save_json),
            ("Toggle selected", self.toggle_selected),
        ]:
            ttk.Button(toolbar, text=label, command=command).pack(
                side="left", padx=(0, 5)
            )

        self.filter_box = ttk.Combobox(
            toolbar, textvariable=self.category,
            values=["All"], state="readonly", width=18,
        )
        self.filter_box.pack(side="right")
        self.filter_box.bind("<<ComboboxSelected>>", lambda event: self.refresh())

        pane = ttk.Panedwindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True, pady=8)
        left = ttk.Frame(pane)
        right = ttk.Frame(pane)
        pane.add(left, weight=2)
        pane.add(right, weight=3)

        self.tree = ttk.Treeview(
            left, columns=("done", "category", "name"), show="headings",
            selectmode="browse",
        )
        for key, title, width in [
            ("done", "Done", 50), ("category", "Category", 110),
            ("name", "Name", 180),
        ]:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width)
        scroll = ttk.Scrollbar(left, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", self.toggle_selected)
        self.tree.bind("<<TreeviewSelect>>", lambda event: self.draw())

        self.canvas = tk.Canvas(right, background="#17212b", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda event: self.draw())
        self.canvas.bind("<Button-1>", self.select_point)

        ttk.Label(
            self,
            text=(
                "CSV columns: id, category, name, x, y "
                "(optional: z, collected). Click a point to select it; "
                "double-click a checklist row to toggle it. "
                "X increases right; Y increases upward."
            ),
            wraplength=950,
        ).pack(anchor="w")
        ttk.Label(self, textvariable=self.summary).pack(anchor="w")
        self.refresh()

    def allow_replace(self):
        return not self.dirty or messagebox.askyesno(
            "Unsaved checklist",
            "Discard unsaved checklist changes?",
            parent=self,
        )

    @staticmethod
    def validate_records(records):
        if not isinstance(records, list):
            raise ValueError("Locations must be a list.")
        result = []
        identifiers = set()
        for number, record in enumerate(records, 1):
            if not isinstance(record, dict):
                raise ValueError(f"Row {number} is not an object.")
            identifier = str(record.get("id", "")).strip()
            if not identifier or identifier in identifiers:
                raise ValueError(f"Row {number}: missing or duplicate id.")
            identifiers.add(identifier)

            category = str(record.get("category", "")).strip()
            name = str(record.get("name", "")).strip()
            if not category or not name:
                raise ValueError(f"Row {number}: category and name are required.")

            coordinates = {}
            for key in ("x", "y", "z"):
                raw = record.get(key, "")
                if key == "z" and (raw is None or raw == ""):
                    coordinates[key] = None
                    continue
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    raise ValueError(f"Row {number}: invalid {key} coordinate.")
                if not math.isfinite(value) or abs(value) > 1e9:
                    raise ValueError(f"Row {number}: unsupported coordinate.")
                coordinates[key] = value

            raw_done = record.get("collected", False)
            if isinstance(raw_done, bool):
                done = raw_done
            else:
                text = str(raw_done).strip().lower()
                if text not in ("", "0", "1", "false", "true", "no", "yes"):
                    raise ValueError(f"Row {number}: invalid collected value.")
                done = text in ("1", "true", "yes")

            result.append({
                "id": identifier, "category": category, "name": name,
                **coordinates, "collected": done,
            })
        return result

    def install_records(self, records, dirty):
        self.items = records
        self.dirty = dirty
        categories = sorted({item["category"] for item in self.items})
        self.filter_box["values"] = ["All"] + categories
        self.category.set("All")
        self.refresh()

    def import_csv(self):
        if not self.allow_replace():
            return
        selected = filedialog.askopenfilename(
            title="Import your collectible locations",
            filetypes=[("CSV locations", "*.csv")],
        )
        if not selected:
            return
        try:
            with open(selected, "r", newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                required = {"id", "category", "name", "x", "y"}
                if not required.issubset(set(reader.fieldnames or [])):
                    raise ValueError(
                        "Required CSV headers: id,category,name,x,y"
                    )
                records = self.validate_records(list(reader))
            self.install_records(records, True)
        except Exception as error:
            messagebox.showerror("Import failed", str(error))

    def open_json(self):
        if not self.allow_replace():
            return
        selected = filedialog.askopenfilename(
            title="Open manual checklist",
            filetypes=[("Checklist JSON", "*.json")],
        )
        if not selected:
            return
        try:
            payload = json.loads(Path(selected).read_text(encoding="utf-8"))
            if (
                not isinstance(payload, dict)
                or payload.get("format") != "sa-de-manual-checklist"
                or payload.get("version") != 1
            ):
                raise ValueError("Unsupported checklist format.")
            records = self.validate_records(payload.get("items"))
            self.install_records(records, False)
        except Exception as error:
            messagebox.showerror("Checklist failed", str(error))

    def save_json(self):
        selected = filedialog.asksaveasfilename(
            title="Save manual checklist (separate from game save)",
            initialfile="collectibles_checklist.json",
            defaultextension=".json",
            filetypes=[("Checklist JSON", "*.json")],
        )
        if not selected:
            return
        try:
            target = Path(selected)
            if target.suffix.lower() != ".json":
                raise ValueError("Checklist filenames must end in .json.")
            if self.app.doc and target.resolve() == self.app.doc.path.resolve():
                raise ValueError("Cannot replace the loaded game save.")

            payload = {
                "format": "sa-de-manual-checklist",
                "version": 1,
                "tracking": "manual-not-linked-to-save",
                "items": self.items,
            }
            target.write_text(
                json.dumps(payload, indent=2, allow_nan=False),
                encoding="utf-8",
            )
            self.dirty = False
            self.refresh()
            self.app.status.set(f"Manual checklist saved: {target}")
        except Exception as error:
            messagebox.showerror("Checklist save failed", str(error))

    def visible_indices(self):
        selected = self.category.get()
        return [
            index for index, item in enumerate(self.items)
            if selected == "All" or item["category"] == selected
        ]

    def refresh(self):
        old_selection = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        visible = self.visible_indices()
        for index in visible:
            item = self.items[index]
            self.tree.insert(
                "", "end", iid=str(index),
                values=(
                    "Yes" if item["collected"] else "No",
                    item["category"], item["name"],
                ),
            )
        if old_selection and self.tree.exists(old_selection[0]):
            self.tree.selection_set(old_selection[0])

        completed = sum(item["collected"] for item in self.items)
        visible_completed = sum(self.items[i]["collected"] for i in visible)
        self.summary.set(
            f"Manual progress: {completed}/{len(self.items)} total; "
            f"{visible_completed}/{len(visible)} in current filter."
            + (" Unsaved checklist changes." if self.dirty else "")
        )
        self.draw()

    def toggle_selected(self, event=None):
        if event is not None:
            row = self.tree.identify_row(event.y)
            if not row:
                return
            self.tree.selection_set(row)
        selected = self.tree.selection()
        if not selected:
            return
        item = self.items[int(selected[0])]
        item["collected"] = not item["collected"]
        self.dirty = True
        self.refresh()

    def draw(self):
        self.canvas.delete("all")
        self.points = []
        width = max(self.canvas.winfo_width(), 120)
        height = max(self.canvas.winfo_height(), 120)
        visible = self.visible_indices()

        if not visible:
            self.canvas.create_text(
                width / 2, height / 2, fill="white", width=width - 30,
                text="No locations loaded.\nImport a locations CSV to begin.",
            )
            return

        # Use the entire dataset's bounds so category changes do not move points.
        xs = [item["x"] for item in self.items]
        ys = [item["y"] for item in self.items]
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        span_x, span_y = max(xmax - xmin, 1.0), max(ymax - ymin, 1.0)
        scale = min((width - 60) / span_x, (height - 80) / span_y)
        center_x, center_y = (xmin + xmax) / 2, (ymin + ymax) / 2
        selected = self.tree.selection()
        chosen = int(selected[0]) if selected else None

        self.canvas.create_text(
            10, 10, anchor="nw", fill="#d9e2ec",
            text="Coordinate plot — not an in-game map",
        )
        for index in visible:
            item = self.items[index]
            x = width / 2 + (item["x"] - center_x) * scale
            y = height / 2 - (item["y"] - center_y) * scale
            radius = 7 if index == chosen else 4
            color = "#65cf8b" if item["collected"] else "#f2bc53"
            self.canvas.create_oval(
                x - radius, y - radius, x + radius, y + radius,
                fill=color, outline="white" if index == chosen else color,
            )
            self.points.append((x, y, index))

        if chosen is not None and chosen in visible:
            item = self.items[chosen]
            self.canvas.create_text(
                10, height - 10, anchor="sw", fill="white",
                width=width - 20,
                text=(
                    f"{item['name']} | X {item['x']:g}, Y {item['y']:g}"
                    + (
                        f", Z {item['z']:g}"
                        if item["z"] is not None else ""
                    )
                ),
            )

    def select_point(self, event):
        if not self.points:
            return
        closest = min(
            self.points,
            key=lambda point: (point[0] - event.x) ** 2
            + (point[1] - event.y) ** 2,
        )
        if (closest[0] - event.x) ** 2 + (closest[1] - event.y) ** 2 <= 225:
            self.tree.selection_set(str(closest[2]))
            self.tree.see(str(closest[2]))
            self.draw()




# BEGIN COLLECTIBLES_V05_UPGRADE
# This extension changes the collectibles UI only.
# The original save-writing functions and App class are preserved.

APP_TITLE = "GTA SA DE Save Editor — Standalone v0.5 preview"


class CollectiblesTabV05(CollectiblesTab):
    """Manual checklist and map workspace; never writes game-save bytes."""

    def __init__(self, parent, app):
        self.search_text = tk.StringVar(master=parent)
        self.progress_filter = tk.StringVar(master=parent, value="All states")
        self.camera = None
        self.drag_start = None
        self.drag_moved = False
        self.undo_stack = []
        self.map_image = None
        self.map_photo = None
        self.map_bounds = None
        self.map_name = ""
        self.render_error = ""
        self._image_api = None
        self._image_tk = None

        super().__init__(parent, app)

        # Replace the inherited descriptive labels, not the original code.
        labels = [
            widget for widget in self.winfo_children()
            if isinstance(widget, ttk.Label)
        ]
        if labels:
            labels[0].configure(
                text=(
                    "Manual collectibles workspace — no bundled locations "
                    "and no game-save collectible flag editing."
                )
            )
        for label in labels:
            if str(label.cget("text")).startswith("CSV columns:"):
                label.configure(
                    text=(
                        "Wheel: zoom | Drag: pan | Click marker: select | "
                        "Double-click checklist row: toggle. "
                        "CSV: id,category,name,x,y; optional z,collected. "
                        "Map image/calibration are session-only."
                    )
                )

        pane = next(
            widget for widget in self.winfo_children()
            if isinstance(widget, ttk.Panedwindow)
        )

        controls = ttk.Frame(self)
        controls.pack(fill="x", pady=(8, 0), before=pane)

        ttk.Label(controls, text="Search:").pack(side="left")
        ttk.Entry(
            controls, textvariable=self.search_text, width=22
        ).pack(side="left", padx=5)

        state_box = ttk.Combobox(
            controls,
            textvariable=self.progress_filter,
            values=["All states", "Uncollected", "Collected"],
            state="readonly",
            width=15,
        )
        state_box.pack(side="left", padx=5)
        state_box.bind(
            "<<ComboboxSelected>>", lambda event: self.refresh()
        )

        for label, command in [
            ("Fit", self.fit),
            ("+", lambda: self.zoom(1.4)),
            ("−", lambda: self.zoom(1 / 1.4)),
            ("Center player", self.center_player),
            ("Undo", self.undo),
        ]:
            ttk.Button(
                controls, text=label, command=command
            ).pack(side="left", padx=2)

        map_controls = ttk.Frame(self)
        map_controls.pack(fill="x", pady=(6, 0), before=pane)
        for label, command in [
            ("Load map image", self.load_map),
            ("Calibrate image", self.calibrate_map),
            ("Remove image", self.remove_map),
        ]:
            ttk.Button(
                map_controls, text=label, command=command
            ).pack(side="left", padx=(0, 5))

        ttk.Label(
            map_controls,
            text="Background must be north-up; calibration is user supplied.",
        ).pack(side="left", padx=5)

        self.search_text.trace_add(
            "write", lambda *arguments: self.refresh()
        )

        self.canvas.bind("<ButtonPress-1>", self.begin_drag)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.end_drag)
        self.canvas.bind("<MouseWheel>", self.wheel)
        self.canvas.bind("<Button-4>", lambda event: self.wheel(event, 1))
        self.canvas.bind("<Button-5>", lambda event: self.wheel(event, -1))
        self.tree.bind("<space>", self.space_toggle)

        self.refresh()

    def install_records(self, records, dirty):
        self.undo_stack.clear()
        self.camera = None
        self.search_text.set("")
        self.progress_filter.set("All states")
        super().install_records(records, dirty)
        self.fit()

    def visible_indices(self):
        category = self.category.get()
        state = self.progress_filter.get()
        query = self.search_text.get().strip().casefold()

        result = []
        for index, item in enumerate(self.items):
            if category != "All" and item["category"] != category:
                continue
            if state == "Collected" and not item["collected"]:
                continue
            if state == "Uncollected" and item["collected"]:
                continue
            searchable = (
                item["id"] + " " + item["name"] + " " + item["category"]
            ).casefold()
            if query and query not in searchable:
                continue
            result.append(index)
        return result

    def refresh(self):
        # The inherited constructor calls this after creating its widgets.
        super().refresh()
        visible = self.visible_indices()
        completed = sum(item["collected"] for item in self.items)
        shown_completed = sum(self.items[i]["collected"] for i in visible)
        self.summary.set(
            f"Manual progress: {completed}/{len(self.items)} total | "
            f"Shown: {shown_completed}/{len(visible)} collected | "
            f"Undo steps: {len(self.undo_stack)}"
            + (" | UNSAVED checklist changes" if self.dirty else "")
        )

    def toggle_selected(self, event=None):
        if event is not None:
            row = self.tree.identify_row(event.y)
            if not row:
                return
            self.tree.selection_set(row)

        selected = self.tree.selection()
        if not selected:
            return

        index = int(selected[0])
        previous = self.items[index]["collected"]
        self.undo_stack.append((index, previous))
        self.items[index]["collected"] = not previous
        self.dirty = True
        self.refresh()

    def space_toggle(self, event):
        self.toggle_selected()
        return "break"

    def undo(self):
        if not self.undo_stack:
            return
        index, previous = self.undo_stack.pop()
        self.items[index]["collected"] = previous
        # Conservative: after undo, prompt to save even if all edits were undone.
        self.dirty = True
        self.refresh()
        row = str(index)
        if self.tree.exists(row):
            self.tree.selection_set(row)
            self.tree.see(row)

    def player_position(self):
        document = self.app.doc
        if document is None or document.state.position is None:
            return None
        x, y, z = document.state.position
        if not all(math.isfinite(value) for value in (x, y, z)):
            return None
        return x, y, z

    def dimensions(self):
        return (
            max(self.canvas.winfo_width(), 120),
            max(self.canvas.winfo_height(), 120),
        )

    def fit(self):
        width, height = self.dimensions()

        if self.map_bounds is not None:
            xmin, ymin, xmax, ymax = self.map_bounds
        elif self.items:
            xs = [item["x"] for item in self.items]
            ys = [item["y"] for item in self.items]
            xmin, xmax = min(xs), max(xs)
            ymin, ymax = min(ys), max(ys)
        else:
            position = self.player_position()
            x, y = position[:2] if position else (0.0, 0.0)
            xmin, xmax = x - 100, x + 100
            ymin, ymax = y - 100, y + 100

        span_x = max(xmax - xmin, 10.0)
        span_y = max(ymax - ymin, 10.0)
        scale = min(
            (width - 60) / span_x,
            (height - 90) / span_y,
        )
        self.camera = [
            (xmin + xmax) / 2,
            (ymin + ymax) / 2,
            max(scale, 1e-12),
        ]
        self.draw()

    def screen_position(self, x, y):
        width, height = self.dimensions()
        cx, cy, scale = self.camera
        return (
            width / 2 + (x - cx) * scale,
            height / 2 - (y - cy) * scale,
        )

    def world_position(self, x, y):
        width, height = self.dimensions()
        cx, cy, scale = self.camera
        return (
            cx + (x - width / 2) / scale,
            cy - (y - height / 2) / scale,
        )

    def zoom(self, factor, x=None, y=None):
        if self.camera is None:
            self.fit()
        width, height = self.dimensions()
        x = width / 2 if x is None else x
        y = height / 2 if y is None else y

        anchor_x, anchor_y = self.world_position(x, y)
        self.camera[2] = min(
            1000.0, max(1e-12, self.camera[2] * factor)
        )
        after_x, after_y = self.world_position(x, y)
        self.camera[0] += anchor_x - after_x
        self.camera[1] += anchor_y - after_y
        self.draw()

    def wheel(self, event, direction=None):
        if direction is None:
            if not event.delta:
                return "break"
            direction = 1 if event.delta > 0 else -1
        self.zoom(1.25 if direction > 0 else 0.8, event.x, event.y)
        return "break"

    def begin_drag(self, event):
        self.canvas.focus_set()
        if self.camera is None:
            self.fit()
        self.drag_start = (
            event.x, event.y, self.camera[0], self.camera[1]
        )
        self.drag_moved = False

    def drag(self, event):
        if self.drag_start is None:
            return
        x, y, cx, cy = self.drag_start
        dx, dy = event.x - x, event.y - y
        if dx * dx + dy * dy > 16:
            self.drag_moved = True
        if self.drag_moved:
            scale = self.camera[2]
            self.camera[0] = cx - dx / scale
            self.camera[1] = cy + dy / scale
            self.draw()

    def end_drag(self, event):
        if self.drag_start is not None and not self.drag_moved:
            self.select_point(event)
        self.drag_start = None

    def center_player(self):
        position = self.player_position()
        if position is None:
            messagebox.showinfo(
                "Player position unavailable",
                "Open a save with a recognized player record first.",
            )
            return
        if self.camera is None:
            self.fit()
        self.camera[0], self.camera[1] = position[:2]
        self.draw()

    @staticmethod
    def ask_bounds(parent, previous=None):
        from tkinter import simpledialog

        initial = (
            ", ".join(format(value, ".9g") for value in previous)
            if previous is not None else ""
        )
        text = simpledialog.askstring(
            "Map coordinate calibration",
            "Enter: xmin, ymin, xmax, ymax\n\n"
            "These must describe the FULL image edges in game coordinates.\n"
            "Left = xmin; right = xmax; top = ymax; bottom = ymin.\n"
            "Use a north-up image without an outside border or legend.\n"
            "Do not guess: incorrect bounds misplace every marker.",
            initialvalue=initial,
            parent=parent,
        )
        if text is None:
            return None
        try:
            values = tuple(float(part.strip()) for part in text.split(","))
        except ValueError:
            raise ValueError("Enter four numbers separated by commas.")
        if len(values) != 4:
            raise ValueError("Exactly four coordinate values are required.")
        if not all(math.isfinite(v) and abs(v) <= 1e9 for v in values):
            raise ValueError("Coordinates must be finite and within ±1e9.")
        xmin, ymin, xmax, ymax = values
        if xmax - xmin < 1e-6 or ymax - ymin < 1e-6:
            raise ValueError("Maximum coordinates must exceed minimums.")
        return values

    def load_map(self):
        try:
            from PIL import Image, ImageTk
        except ImportError:
            messagebox.showinfo(
                "Optional Pillow dependency",
                "Map backgrounds require Pillow; the coordinate plot does not.\n\n"
                "On Windows, install it for the Python running this editor:\n"
                "py -m pip install Pillow\n\n"
                "Then restart the editor and load your map image.",
            )
            return

        selected = filedialog.askopenfilename(
            title="Select a local north-up map image",
            filetypes=[
                ("Map image", "*.png *.jpg *.jpeg *.bmp"),
                ("All files", "*.*"),
            ],
        )
        if not selected:
            return

        try:
            with Image.open(selected) as source:
                if source.width * source.height > 40_000_000:
                    raise ValueError("Use an image of 40 megapixels or less.")
                source.load()
                image = source.convert("RGB")

            bounds = self.ask_bounds(self, self.map_bounds)
            if bounds is None:
                return

            self._image_api = Image
            self._image_tk = ImageTk
            self.map_image = image
            self.map_bounds = bounds
            self.map_name = Path(selected).name
            self.render_error = ""
            self.fit()
        except Exception as error:
            messagebox.showerror("Map image failed", str(error))

    def calibrate_map(self):
        if self.map_image is None:
            messagebox.showinfo("Map image", "Load a map image first.")
            return
        try:
            bounds = self.ask_bounds(self, self.map_bounds)
            if bounds is not None:
                self.map_bounds = bounds
                self.render_error = ""
                self.fit()
        except Exception as error:
            messagebox.showerror("Calibration failed", str(error))

    def remove_map(self):
        self.map_image = None
        self.map_photo = None
        self.map_bounds = None
        self.map_name = ""
        self.render_error = ""
        self.fit()

    def draw_background(self, width, height):
        if self.map_image is None or self.render_error:
            return
        try:
            xmin, ymin, xmax, ymax = self.map_bounds
            left, top = self.screen_position(xmin, ymax)
            scale = self.camera[2]

            # Output screen pixels -> source-image pixels.
            # Transform only the viewport; do not allocate a giant zoomed map.
            x_factor = self.map_image.width / ((xmax - xmin) * scale)
            y_factor = self.map_image.height / ((ymax - ymin) * scale)
            affine = (
                x_factor, 0, -left * x_factor,
                0, y_factor, -top * y_factor,
            )
            image = self.map_image.transform(
                (width, height),
                self._image_api.Transform.AFFINE,
                affine,
                resample=self._image_api.Resampling.BILINEAR,
                fillcolor=(23, 33, 43),
            )
            self.map_photo = self._image_tk.PhotoImage(
                image, master=self.canvas
            )
            self.canvas.create_image(
                0, 0, anchor="nw", image=self.map_photo
            )
        except Exception as error:
            self.render_error = str(error)
            self.app.status.set(
                "Map background rendering failed; plot remains available: "
                + self.render_error
            )

    def draw(self):
        if self.camera is None:
            self.fit()
            return

        self.canvas.delete("all")
        self.points = []
        width, height = self.dimensions()
        self.draw_background(width, height)

        visible = self.visible_indices()
        selected = self.tree.selection()
        chosen = int(selected[0]) if selected else None

        # Selected marker is drawn last so it remains visible in a cluster.
        order = [index for index in visible if index != chosen]
        if chosen in visible:
            order.append(chosen)

        for index in order:
            item = self.items[index]
            x, y = self.screen_position(item["x"], item["y"])
            if not (-10 <= x <= width + 10 and -10 <= y <= height + 10):
                continue
            radius = 7 if index == chosen else 4
            color = "#65cf8b" if item["collected"] else "#f2bc53"
            self.canvas.create_oval(
                x - radius, y - radius, x + radius, y + radius,
                fill=color,
                outline="white" if index == chosen else "#15202b",
                width=2,
            )
            self.points.append((x, y, index))

        player = self.player_position()
        if player is not None:
            px, py = self.screen_position(*player[:2])
            if 0 <= px <= width and 0 <= py <= height:
                self.canvas.create_polygon(
                    px, py - 9, px + 8, py + 7, px - 8, py + 7,
                    fill="#66d9ff", outline="white", width=2,
                )
                self.canvas.create_text(
                    px + 12, py, anchor="w", fill="white", text="Player"
                )

        mode = (
            "Local map — user-calibrated"
            if self.map_image is not None and not self.render_error
            else "Coordinate plot — no map background"
        )
        self.canvas.create_rectangle(
            0, 0, width, 29, fill="#17212b", outline=""
        )
        self.canvas.create_text(
            10, 14, anchor="w", fill="white",
            text=f"{mode} | Yellow: pending | Green: collected",
        )

        if not self.items:
            self.canvas.create_text(
                width / 2, height / 2, fill="white", width=width - 40,
                text=(
                    "No collectible locations loaded.\n"
                    "Import a locations CSV or open your checklist JSON.\n"
                    "No verified location dataset is bundled."
                ),
            )
        elif not visible:
            self.canvas.create_text(
                width / 2, height / 2, fill="white",
                text="No collectibles match the current filters.",
            )

        if chosen is not None and chosen in visible:
            item = self.items[chosen]
            coordinates = f"X {item['x']:g}, Y {item['y']:g}"
            if item["z"] is not None:
                coordinates += f", Z {item['z']:g}"
            self.canvas.create_rectangle(
                0, height - 56, width, height,
                fill="#17212b", outline="",
            )
            self.canvas.create_text(
                10, height - 8, anchor="sw", fill="white",
                width=width - 20,
                text=f"{item['id']} | {item['name']}\n{coordinates}",
            )

    def select_point(self, event):
        width, height = self.dimensions()
        if event.y < 29:
            return
        if self.tree.selection() and event.y > height - 56:
            return
        super().select_point(event)


# App resolves this global when constructing its collectibles tab.
# The original class remains available as this subclass's base.
CollectiblesTab = CollectiblesTabV05

# END COLLECTIBLES_V05_UPGRADE

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1100x780")
        self.minsize(900, 650)
        self.doc = None
        self.variables = {
            key: tk.StringVar() for key in ("money", "health", "armor", "mission")
        }
        self.entries = {}
        self.file_text = tk.StringVar(value="No save loaded")
        self.info = tk.StringVar()
        self.status = tk.StringVar(
            value="Open a save. Collectibles tracking is separate and manual."
        )

        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        for label, action in [
            ("Open Save", self.open_save),
            ("Reload", self.reload),
            ("Save As…", self.save_as),
        ]:
            ttk.Button(top, text=label, command=action).pack(
                side="left", padx=(0, 6)
            )
        ttk.Label(top, textvariable=self.file_text).pack(side="left", padx=10)

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        player_tab = ttk.Frame(notebook, padding=12)
        blocks_tab = ttk.Frame(notebook, padding=10)
        notebook.add(player_tab, text="Player")
        notebook.add(blocks_tab, text="Block inspection")
        self.collectibles = CollectiblesTab(notebook, self)
        notebook.add(self.collectibles, text="Collectibles — manual")

        ttk.Label(
            player_tab, textvariable=self.info, wraplength=980,
            justify="left",
        ).pack(anchor="w", pady=(0, 15))

        fields = ttk.Frame(player_tab)
        fields.pack(anchor="w")
        for row, (key, label) in enumerate([
            ("money", "Money"),
            ("health", "Health"),
            ("armor", "Armor"),
            ("mission", "Last-mission GXT key (same byte length only)"),
        ]):
            ttk.Label(fields, text=label + ":").grid(
                row=row, column=0, sticky="w", pady=7
            )
            entry = ttk.Entry(fields, textvariable=self.variables[key], width=30)
            entry.grid(row=row, column=1, sticky="w", padx=12, pady=7)
            entry.configure(state="disabled")
            self.entries[key] = entry

        ttk.Label(
            player_tab,
            text=(
                "Edit the fields, then click Save As. There are no Apply buttons "
                "in this standalone version.\n"
                "Only changed fields are patched; the checksum is recalculated.\n"
                "The mission key is a label field, not a mission-progress editor.\n\n"
                "Health/armor use the previously tested POOLS signature. "
                "Other layouts are disabled instead of guessed."
            ),
            wraplength=950, justify="left",
        ).pack(anchor="w", pady=20)

        ttk.Label(
            blocks_tab,
            text=(
                "Legacy heuristic scanner: reported sizes are not authoritative. "
                "POOLS preview deliberately shows bytes beyond the reported size."
            ),
            wraplength=950,
        ).pack(anchor="w", pady=(0, 8))
        pane = ttk.Panedwindow(blocks_tab, orient="horizontal")
        pane.pack(fill="both", expand=True)
        self.block_list = tk.Listbox(pane, width=38, exportselection=False)
        preview_frame = ttk.Frame(pane)
        self.preview = tk.Text(
            preview_frame, wrap="none", font=("Consolas", 10), state="disabled"
        )
        scroll = ttk.Scrollbar(preview_frame, command=self.preview.yview)
        self.preview.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.preview.pack(fill="both", expand=True)
        pane.add(self.block_list, weight=1)
        pane.add(preview_frame, weight=3)
        self.block_list.bind("<<ListboxSelect>>", self.show_block)

        ttk.Label(
            self, textvariable=self.status, anchor="w",
            relief="sunken", padding=5,
        ).pack(fill="x", side="bottom")
        self.protocol("WM_DELETE_WINDOW", self.close)

    def values(self):
        return {key: variable.get() for key, variable in self.variables.items()}

    def has_edits(self):
        return self.doc is not None and self.values() != self.doc.display

    def allow_discard(self):
        return not self.has_edits() or messagebox.askyesno(
            "Unsaved save edits", "Discard the edits currently in the fields?"
        )

    def open_save(self):
        if not self.allow_discard():
            return
        selected = filedialog.askopenfilename(
            title="Open GTA SA DE save",
            initialdir=str(default_save_folder(self.doc.path if self.doc else None)),
            filetypes=[("GTA SA DE saves", "*.sav"), ("All files", "*.*")],
        )
        if selected:
            self.load_path(selected)

    def reload(self):
        if self.doc and self.allow_discard():
            self.load_path(self.doc.path)

    def load_path(self, path):
        try:
            document = SaveDocument(path)
            if not document.valid_checksum and not messagebox.askyesno(
                "Checksum mismatch",
                "The save does not match the existing editor's checksum "
                "algorithm.\n\nOpen it anyway? Keep an untouched backup.",
            ):
                return
            self.install_document(document)
        except Exception as error:
            messagebox.showerror("Open failed", str(error))

    def install_document(self, document):
        self.doc = document
        self.file_text.set(str(document.path))
        for key, variable in self.variables.items():
            variable.set(document.display[key])
            available = (
                key not in FIELD_OFFSETS or document.player_base is not None
            )
            self.entries[key].configure(
                state="normal" if available else "disabled"
            )

        lines = [
            f"Version field: {document.version} | "
            f"Header checksum: {'VALID' if document.valid_checksum else 'MISMATCH'}",
            f"Money mirror: {document.state.money_mirror:,}",
        ]
        if document.player_base is not None:
            x, y, z = document.state.position
            lines.extend([
                f"Recognized player record: 0x{document.player_base:X}",
                f"Position (read-only): X {x:.6g}, Y {y:.6g}, Z {z:.6g}",
            ])
        else:
            lines.append("Health/armor unavailable: " + document.player_error)
        self.info.set("\n".join(lines))

        self.block_list.delete(0, "end")
        for name, _, offset, size in document.blocks:
            self.block_list.insert(
                "end", f"{name} | 0x{offset:X} | reported {size:,}"
            )
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.configure(state="disabled")
        self.status.set(
            f"Loaded {len(document.data):,} bytes. "
            "Collectibles checklist is not linked to this save."
        )

    def show_block(self, event=None):
        selected = self.block_list.curselection()
        if self.doc is None or not selected:
            return
        name, name_offset, offset, size = self.doc.blocks[selected[0]]
        if name == "POOLS":
            start = name_offset
            end = min(len(self.doc.data), start + 4096)
        else:
            start = offset
            end = min(len(self.doc.data), offset + min(size, 4096))
        lines = [
            f"{name}: reported data offset 0x{offset:X}, reported size {size}",
            "Raw preview; legacy size interpretation may be incorrect.",
            "",
        ]
        for position in range(start, end, 16):
            chunk = self.doc.data[position:min(position + 16, end)]
            hex_text = " ".join(f"{value:02X}" for value in chunk)
            text = "".join(chr(v) if 32 <= v < 127 else "." for v in chunk)
            lines.append(f"{position:08X}  {hex_text:<47}  {text}")
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", "\n".join(lines))
        self.preview.configure(state="disabled")

    def save_as(self):
        if self.doc is None:
            messagebox.showinfo("Save", "Open a save first.")
            return

        try:
            output, edits = self.doc.build_output(self.values())
        except Exception as error:
            messagebox.showerror("Check your edits", str(error))
            return

        selected = filedialog.asksaveasfilename(
            title="Write a NEW edited save",
            initialdir=str(default_save_folder(self.doc.path)),
            initialfile=self.doc.path.stem + "_edited.sav",
            defaultextension=".sav",
            filetypes=[("GTA SA DE saves", "*.sav")],
        )
        if not selected:
            return

        target = Path(selected)
        created = False
        try:
            if target.resolve() == self.doc.path.resolve():
                raise ValueError("The loaded source is protected. Use a new name.")
            if target.exists():
                raise ValueError("Choose a new filename; existing files are protected.")

            with target.open("xb") as handle:
                created = True
                handle.write(output)

            verified = SaveDocument(target)
            if verified.data != output:
                raise ValueError("Written bytes do not match the output.")
            if not verified.valid_checksum:
                raise ValueError("Checksum failed after reopening.")

            for field, expected in edits.items():
                if field == "money":
                    if (
                        verified.state.money != expected
                        or verified.state.money_mirror != expected
                    ):
                        raise ValueError("Money read-back failed.")
                elif field in FIELD_OFFSETS:
                    if getattr(verified.state, field) != expected:
                        raise ValueError(f"{field.title()} read-back failed.")
                elif verified.display["mission"] != expected:
                    raise ValueError("Mission key read-back failed.")

            changes = sum(
                before != after
                for index, (before, after) in enumerate(
                    zip(self.doc.data, verified.data)
                )
                if not 8 <= index < 24
            )
            edited_fields = ", ".join(edits) or "none (checksum-only round trip)"
            self.install_document(verified)
            self.status.set(f"Saved and reopened: {target}")
            messagebox.showinfo(
                "Saved and verified",
                f"Fields edited: {edited_fields}\n"
                f"Changed bytes outside checksum: {changes}\n\n"
                "Output reopened and checked successfully.\n"
                "The source file was not modified.\n"
                "The editor now displays the newly written file.\n\n"
                "File checks do not replace an in-game test.",
            )
        except Exception as error:
            warning = (
                "\n\nAn output file was created but did not finish verification. "
                "Do not use that output."
                if created else ""
            )
            messagebox.showerror("Save failed", str(error) + warning)

    def close(self):
        if not self.allow_discard():
            return
        if not self.collectibles.allow_replace():
            return
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
