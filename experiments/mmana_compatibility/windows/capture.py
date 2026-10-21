"""Collect MMANA-GAL reference artifacts using its Windows GUI, outside the Agent.

Unknown controls stop collection and produce diagnostics. A successful capture
is evidence for review, never a solver compatibility verdict.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from recipes import ALL_CASES, Case, capture_plan, check_saved_model

T = TypeVar("T")
TIMEOUT = 90
GEOMETRY_TAB = r"^(结构|結構|Geometry)$"
CALC_TAB = r"^(计算|計算|Calculate|Calculation)$"
START = r"^(开始|開始|Start)$"
SUCCESS = r"无计算错误|無計算錯誤|No\s+(?:calculation\s+)?errors?"
FAILED = r"计算(?:发生错误|中断)|計算中断|Calculation\s+(?:error|aborted)|Error\s+in\s+calculation"
# Dlgs.h names both filename layouts in the Windows common file dialog.
FILE_NAME_EDIT_ID = 0x0480  # edt1
FILE_NAME_COMBO_ID = 0x047C  # cmb13
# Fixed single-medium recipe, in the displayed ground table's column order.
# X=0 is a recorded GUI value, not a claim that the ground ends at zero metres.
SOIL_FIELDS = (("epsilon_r", 13.0), ("conductivity_ms_per_m", 5.0), ("x_m", 0.0), ("height_m", 0.0))


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wait_for(description: str, read: Callable[[], T], timeout: float = TIMEOUT) -> T:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = read()
        if result:
            return result
        time.sleep(0.2)
    raise TimeoutError(description)


def caption(text: str) -> str:
    text = re.sub(r"\(&?[A-Za-z]\)", "", text)
    return text.replace("&", "").split("\t")[0].strip().rstrip(". …")


def unique(items: list[T], description: str) -> T:
    if len(items) != 1:
        raise RuntimeError(f"{description}: expected one control, found {len(items)}")
    return items[0]


def installation_copy(exe: Path, target: Path) -> Path:
    """Copy program/resources, not models, so GAL writes to its own disposable directory."""
    if exe.parent == Path(exe.anchor):
        raise ValueError("Put MMANA-GAL in its own installation folder, not a drive root.")
    allowed = {
        ".exe",
        ".dll",
        ".ini",
        ".lng",
        ".lang",
        ".mmn",
        ".txt",
        ".dat",
        ".cfg",
        ".chm",
        ".hlp",
        ".ico",
        ".bmp",
    }
    files: list[Path] = []
    size = 0
    for directory, folders, names in os.walk(exe.parent, followlinks=False):
        folder = Path(directory)
        # Resource folders only. Do not follow Windows junctions into other user directories.
        folders[:] = [
            name
            for name in folders
            if name.lower() in {"lang", "langs", "language", "languages", "locale", "resources"}
            and not (folder / name).lstat().st_file_attributes & 0x400
        ]
        for name in names:
            source = folder / name
            if source.lstat().st_file_attributes & 0x400:
                continue
            if source.suffix.lower() not in allowed or (source.suffix.lower() == ".exe" and source != exe):
                continue
            size += source.stat().st_size
            files.append(source)
            if len(files) > 2000 or size > 128 * 1024 * 1024:
                raise ValueError("Installation copy exceeds 128 MiB / 2000 files; use a dedicated GAL folder.")
    for source in files:
        destination = target / source.relative_to(exe.parent)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    staged = target / exe.name
    if not staged.is_file():
        raise ValueError("MMANA-GAL executable could not be staged.")
    return staged


class Capture:
    """Own one GUI process; dynamic pywinauto wrappers remain inside this UI boundary."""

    def __init__(self, exe: Path, output: Path) -> None:
        from pywinauto import Application
        from pywinauto.controls.win32_controls import ButtonWrapper, ComboBoxWrapper, EditWrapper

        self.button_wrapper = ButtonWrapper
        self.combo_wrapper = ComboBoxWrapper
        self.edit_wrapper = EditWrapper
        self.app = Application(backend="win32")
        self.output = output
        self.exe = exe
        self.main: Any = None

    def start(self, model: Path) -> None:
        command = subprocess.list2cmdline([str(self.exe), str(model)])
        self.app.start(command, work_dir=str(self.exe.parent))

        def ready_main() -> Any:
            if not self.app.is_process_running():
                raise RuntimeError("MMANA-GAL exited before its main window was ready.")
            # GAL 1.2.0.20 has more than one window titled MMANA. Its actual
            # editor is TMainWnd; app.windows() restricts the search to our PID.
            windows = self.app.windows(class_name="TMainWnd", visible_only=True, enabled_only=False)
            if not windows:
                return None
            window = unique(windows, "Visible MMANA editor")
            self.main = window
            self.require_no_dialog("opening the model")
            # The title and child HWNDs can exist while GAL still hides its entire
            # page control during loading. Use the same readiness gate as tab().
            # Start itself is created lazily on Calculation, so do not wait for it here.
            if model.stem.lower() in window.window_text().lower() and self.ready_page_control() is not None:
                return window
            return None

        # Keep only a resolved wrapper, never an ambiguous WindowSpecification;
        # failure diagnostics must still work when startup has not completed.
        self.main = wait_for("MMANA model title and visible editor page did not become ready", ready_main, 30)
        print("[MMANA] Model opened; activating Calculation", flush=True)
        self.tab(CALC_TAB)

        def calculation_visible() -> bool:
            self.require_no_dialog("activating Calculation")
            buttons = [
                control
                for control in self.controls(self.main, r"TButton|TBitBtn|Button")
                if re.fullmatch(START, caption(control.window_text()), re.IGNORECASE)
            ]
            return bool(buttons) and unique(buttons, "Calculation Start").is_enabled()

        wait_for("Calculation page did not become ready", calculation_visible, 15)
        print("[MMANA] Calculation page ready", flush=True)

    def controls(self, parent: Any, class_pattern: str) -> list[Any]:
        return [
            item
            for item in parent.descendants()
            if item.is_visible() and re.fullmatch(class_pattern, item.class_name(), re.IGNORECASE)
        ]

    def button(self, parent: Any, pattern: str) -> Any:
        return unique(
            [
                item
                for item in self.controls(parent, r"TButton|TBitBtn|Button|TRadioButton|TGroupButton|TCheckBox")
                if re.fullmatch(pattern, caption(item.window_text()), re.IGNORECASE)
            ],
            f"Button {pattern}",
        )

    def click(self, button: Any) -> None:
        # WM mouse messages target this HWND even when PowerShell is foreground.
        # Use the client centre so borders and DPI do not change the hit target.
        wrapper = self.button_wrapper(button.handle)
        rect = wrapper.client_rect()
        wrapper.click(coords=(rect.width() // 2, rect.height() // 2))

    def ready_page_control(self) -> Any:
        """Return the usable editor, not merely a container whose HWND was allocated."""
        if not self.app.is_process_running():
            raise RuntimeError("MMANA-GAL exited while waiting for its editor.")
        self.require_no_dialog("waiting for the editor")
        if not self.main.is_visible() or not self.main.is_enabled():
            return None
        # Enumerate hidden containers too: absent and allocated-but-hidden are both
        # pending states, while multiple containers remain an unsupported layout.
        candidates = [item for item in self.main.children() if item.class_name() == "TPageControl"]
        if not candidates:
            return None
        tabs = unique(candidates, "MMANA page control")
        if not tabs.is_visible() or not tabs.is_enabled():
            return None
        return tabs if self.active_page(tabs) is not None else None

    def active_page(self, tabs: Any) -> Any:
        # Refresh child HWNDs after every switch; GAL creates pages lazily.
        self.require_no_dialog("waiting for an active page")
        visible = [item for item in tabs.children() if item.class_name() == "TTabSheet" and item.is_visible()]
        if not visible:
            return None
        page = unique(visible, "Active page")
        return page if page.is_enabled() else None

    def tab(self, pattern: str) -> None:
        from pywinauto.controls.common_controls import TabControlWrapper
        from pywinauto.win32defines import TCS_BUTTONS

        tabs = wait_for("MMANA page control stayed missing, hidden or disabled", self.ready_page_control, 30)
        selector = TabControlWrapper(tabs.handle)
        # select(int) on normal tabs sends the scalar TCM_SETCURFOCUS message.
        # Button-style tabs use another SDK path; do not use that or get_tab_text(),
        # texts(), select(str), which can send pointer-bearing remote structures.
        if selector.has_style(TCS_BUTTONS):
            raise RuntimeError("Button-style page controls are not supported by this capture script.")

        active = wait_for("No visible page", lambda: self.active_page(tabs), 5)
        observed = [caption(active.window_text())]
        if re.fullmatch(pattern, observed[-1], re.IGNORECASE):
            return
        # Child HWND order is Z-order, not tab order. Visit the actual tab indices
        # once, checking both the selected index and the newly visible page caption.
        for index in range(selector.tab_count()):
            if index == selector.get_selected_tab():
                continue
            previous_handle = active.handle
            selector.select(index)

            def changed(previous: int = previous_handle, requested: int = index) -> Any:
                current = self.active_page(tabs)
                if selector.get_selected_tab() == requested and current is not None and current.handle != previous:
                    return current
                return None

            active = wait_for(f"Tab index {index} did not activate its page", changed, 5)
            observed.append(caption(active.window_text()))
            print(f"[MMANA] Tab {index}: {observed[-1]}", flush=True)
            if re.fullmatch(pattern, observed[-1], re.IGNORECASE):
                return
        raise RuntimeError(f"Page {pattern} not found; observed pages: {observed}")

    def send_keys(self, control: Any, keys: str) -> None:
        # The VCL grid needs navigation keys. Keep keyboard focus inside its owning
        # GUI thread, but post keys to the HWND instead of using global SendInput.
        # Callers verify the resulting editor/value; posting keys is not success.
        control.verify_actionable()
        control.set_keyboard_focus()
        if not control.has_keyboard_focus():
            raise RuntimeError(f"Could not focus {control.class_name()} for keyboard input.")
        control.send_keystrokes(keys)
        control.wait_for_idle()

    def menus(self) -> list[tuple[list[str], Any]]:
        result: list[tuple[list[str], Any]] = []

        def visit(menu: Any, parents: list[str]) -> None:
            for item in menu.items():
                label = caption(item.text())
                path = [*parents, label]
                submenu = item.sub_menu()
                if submenu:
                    visit(submenu, path)
                elif label:
                    result.append((path, item))

        visit(self.main.menu(), [])
        return result

    def menu(self, pattern: str) -> None:
        matches = [
            (path, item)
            for path, item in self.menus()
            if len(path) == 2
            and re.search(pattern, path[-1], re.IGNORECASE)
            and re.fullmatch(r"文件|檔案|File", path[0], re.IGNORECASE)
        ]
        path, item = unique(matches, f"Menu {pattern}")
        if not item.is_enabled():
            raise RuntimeError(f"Menu is disabled: {path}")
        # MenuItem.select() also calls SetForegroundWindow in pywinauto 0.6.9.
        # Post the discovered command ID instead: a synchronous send can block
        # on the Save dialog that this same collector must subsequently handle.
        self.main.verify_actionable()
        command = item.item_id()
        if not 0 < command <= 0xFFFF:
            raise RuntimeError(f"Invalid file menu command ID: {command}")
        if not self.main.post_command(command):
            raise RuntimeError(f"Could not post file menu command: {path}")

    def dialog(self) -> Any:
        return wait_for("A single owned dialog did not appear", self._dialog, 15)

    def _dialog(self) -> Any:
        windows = [
            window
            for window in self.app.windows()
            if self.is_ui_window(window) and window.is_enabled() and window.handle != self.main.handle
        ]
        if len(windows) > 1:
            titles = [(window.class_name(), window.window_text()) for window in windows]
            raise RuntimeError(f"Multiple unexpected dialogs; refusing to guess: {titles}")
        return windows[0] if windows else None

    def require_no_dialog(self, stage: str) -> None:
        dialog = self._dialog()
        if dialog is not None:
            text = [dialog.window_text(), *[item.window_text() for item in self.controls(dialog, r"Static|TLabel")]]
            raise RuntimeError(f"Unexpected {dialog.class_name()} dialog while {stage}: {' | '.join(text)}")

    @staticmethod
    def is_ui_window(window: Any) -> bool:
        # VCL's TApplication helper can have WS_VISIBLE without a drawable window.
        # It is not a dialog; genuine error/save dialogs must still stop or be handled.
        if not window.is_visible() or window.class_name() in {"TApplication", "TPUtilWindow", "ComboLBox"}:
            return False
        rect = window.rectangle()
        return rect.right > rect.left and rect.bottom > rect.top

    def set_edit(self, edit: Any, value: str) -> None:
        wrapper = self.edit_wrapper(edit.handle)
        wrapper.set_edit_text(value)
        if wrapper.window_text() != value:
            raise RuntimeError("Edit control did not retain the requested value.")

    def save_screenshot(self, window: Any, name: str) -> None:
        from PIL import ImageGrab

        if not window.handle or window.process_id() != self.app.process:
            raise RuntimeError("Refusing to capture a window outside the owned MMANA process.")
        # pywinauto captures desktop pixels inside a rectangle, including any
        # covering app. Pillow 12's HWND capture renders the specific GAL window.
        with ImageGrab.grab(window=window.handle) as screenshot:
            screenshot.save(self.output / name)

    def file_dialog(self, path: Path) -> None:
        from pywinauto.win32defines import IDOK

        if path.exists():
            raise FileExistsError(path)
        dialog = self.dialog()
        if dialog.class_name() != "#32770":
            raise RuntimeError("Expected a native Save dialog, not an application or confirmation dialog.")

        def ready_fields() -> tuple[Any, Any] | None:
            # Match standard controls owned directly by the dialog. Descendant
            # IDs are not globally unique (the folder list also has ID 1).
            children = [item for item in dialog.children() if item.is_visible() and item.is_enabled()]
            fields = []
            for item in children:
                if item.control_id() == FILE_NAME_EDIT_ID and item.class_name() == "Edit":
                    fields.append(item)
                elif item.control_id() == FILE_NAME_COMBO_ID and item.class_name() in {"ComboBox", "ComboBoxEx32"}:
                    fields.extend(edit for edit in self.controls(item, r"Edit") if edit.is_enabled())
            buttons = [item for item in children if item.control_id() == IDOK and item.class_name() == "Button"]
            # Creation of the top-level dialog does not mean its child controls
            # are ready. Missing controls wait; ambiguous layouts fail closed.
            field = unique(fields, "Save dialog filename") if fields else None
            save = unique(buttons, "Save dialog confirm") if buttons else None
            return (field, save) if field is not None and save is not None else None

        field, save = wait_for(
            "Save dialog filename (edt1/cmb13) and confirm button did not become ready", ready_fields, 15
        )
        self.set_edit(field, str(path))
        # Recheck immediately before confirming: the collector never authorizes
        # overwriting an existing artifact or dismissing an overwrite prompt.
        if path.exists():
            raise FileExistsError(path)
        self.click(save)
        wait_for("Save dialog stayed open", lambda: not dialog.is_visible(), 15)

    def save_model(self, path: Path) -> None:
        self.menu(r"另存|Save\s+(?:as|antenna\s+as)")
        self.file_dialog(path)
        # Read back the model only after the dialog is gone and the file is stable,
        # using the same completion check as CSV exports.
        self.wait_export(path)

    def configure(self, case: Case, frequency: float) -> dict[str, object]:
        frequency_controls = self.set_frequency(case.name, frequency)
        ground_patterns = (
            r"自由空间|自由空間|Free\s+space",
            r"理想(?:地面)?|Perfect(?:\s+ground)?|Ideal",
            r"实际(?:地面)?|實際|Real(?:\s+ground)?",
        )
        ground = self.button(self.main, ground_patterns[case.ground])
        self.click(ground)
        wait_for(
            "Ground radio selection could not be verified",
            lambda: self.button_wrapper(ground.handle).get_check_state() == 1,
            5,
        )
        combos = [self.combo_wrapper(item.handle) for item in self.controls(self.main, r"TComboBox|ComboBox")]
        materials = [
            combo
            for combo in combos
            if any(re.search(r"铜|銅|copper", label, re.IGNORECASE) for label in combo.item_texts())
        ]
        material = unique(materials, "Material selector")
        expected = (
            r"铜(?:线)?|銅(?:線)?|Copper\s+wire|Copper" if case.copper else r"无损耗|无损|無損耗|No\s+loss|Lossless"
        )
        index = unique(
            [
                index
                for index, label in enumerate(material.item_texts())
                if re.fullmatch(expected, caption(label), re.IGNORECASE)
            ],
            "Material preset",
        )
        material.select(index)
        if material.selected_index() != index:
            raise RuntimeError("Material selection did not persist.")
        settings: dict[str, object] = {
            "frequency_mhz": frequency,
            "frequency_controls": frequency_controls,
            "ground_caption": ground.window_text(),
            "material_caption": material.selected_text(),
            "material_conductivity_s_per_m": None,
            "material_note": "GAL preset; numerical conductivity must be independently identified.",
        }
        if case.ground == 2:
            settings["soil"] = self.configure_soil()
            settings["soil_verification"] = "GUI readback after OK and reopening; screenshot requires reference review"
        self.save_screenshot(self.main, "settings.png")
        return settings

    def geometry_frequency(self, model_name: str) -> Any:
        self.tab(GEOMETRY_TAB)
        page = unique(self.controls(self.main, r"TTabSheet"), "Geometry page")
        children = [item for item in page.children() if item.is_visible() and item.is_enabled()]
        # In the captured GAL layout the name and design frequency share a row;
        # DM1/DM2/SC/EC are below it. Restrict the search to direct page children
        # so combo editors and grid editors cannot be mistaken for the name.
        name = unique(
            [item for item in children if item.class_name() == "TEdit" and item.window_text() == model_name],
            f"Geometry name field containing {model_name}",
        )
        name_rect = name.rectangle()
        return unique(
            [
                item
                for item in children
                if item.class_name() in {"TComboBox", "ComboBox"}
                and item.rectangle().left >= name_rect.right
                and item.rectangle().top < name_rect.bottom
                and item.rectangle().bottom > name_rect.top
            ],
            "Geometry frequency combo beside the name",
        )

    def calculation_frequency(self) -> Any:
        self.tab(CALC_TAB)
        ground_group = self.button(self.main, r"自由空间|自由空間|Free\s+space").parent()
        if ground_group.class_name() != "TRadioGroup":
            raise RuntimeError("Unrecognized calculation ground group.")
        combos = self.controls(self.main, r"TComboBox|ComboBox")
        return unique(
            [item for item in combos if item.rectangle().bottom <= ground_group.rectangle().top],
            "Calculation frequency combo above the ground selector",
        )

    def set_frequency(self, model_name: str, frequency: float) -> dict[str, float]:
        from pywinauto.win32defines import CBN_EDITCHANGE, CBN_KILLFOCUS

        # GAL saves Geometry's frequency, independently of Calculation's frequency.
        # Set the design first (its change handler can update Calculation), then
        # Calculation. The saved .maa and the value at Start are checked separately.
        observed: dict[str, float] = {}
        for label, locate in (
            ("geometry", lambda: self.geometry_frequency(model_name)),
            ("calculation", self.calculation_frequency),
        ):
            field = locate()
            edit = unique(self.controls(field, r"TEdit|Edit"), f"{label} frequency edit")
            previous = edit.window_text()
            self.set_edit(edit, f"{frequency:.3f}")
            combo = self.combo_wrapper(field.handle)
            combo.notify_parent(CBN_EDITCHANGE)
            combo.notify_parent(CBN_KILLFOCUS)
            combo.wait_for_idle()
            actual = float(edit.window_text())
            if not math.isclose(actual, frequency, rel_tol=0, abs_tol=1e-6):
                raise RuntimeError(f"{label} frequency: expected {frequency:g} MHz, read back {actual:g} MHz.")
            observed[f"{label}_mhz"] = actual
            print(f"[MMANA] {label} frequency: {previous} -> {actual:.3f} MHz", flush=True)
        return observed

    def open_soil(self) -> Any:
        self.click(
            self.button(
                self.main,
                r"地面设置|地面設定|地面参数|设置地面|设置|設定|Ground\s+setup|Ground\s+settings|Setup",
            )
        )
        dialog = self.dialog()
        if dialog.class_name() != "TMediaDlgBox":
            raise RuntimeError(f"Expected the ground setup dialog, got {dialog.class_name()}.")
        return dialog

    def soil_controls(self, dialog: Any) -> tuple[Any, Any, list[Any]]:
        grid = unique(self.controls(dialog, r"TStringGrid|TDrawGrid"), "Ground grid")
        check = unique(self.controls(dialog, r"TCheckBox"), "Ground screen checkbox")
        label = caption(check.window_text())
        # The captured Chinese GAL 1.2.0.20 labels GScrFlag as '地面介质类型'.
        # Require its radial-count/radius editor group too; a translated caption
        # alone is not permission to disable an unrelated ground option.
        if not re.search(r"地网|地網|radial|screen|^地面介质类型$|^地面介質類型$", label, re.IGNORECASE):
            raise RuntimeError(f"Unknown ground checkbox: {label}")
        group = check.parent()
        if group.class_name() != "TGroupBox":
            raise RuntimeError("Ground screen checkbox is not inside its settings group.")
        radial_edits = self.controls(group, r"TEdit")
        if len(radial_edits) != 2:
            raise RuntimeError("Expected the radial count and radius editors in the ground screen group.")
        return grid, check, radial_edits

    def close_soil(self, dialog: Any, *, confirm: bool) -> None:
        from pywinauto.win32defines import BN_CLICKED

        action = "confirm" if confirm else "cancel verification"
        if dialog.class_name() != "TMediaDlgBox" or dialog.process_id() != self.app.process:
            raise RuntimeError("Refusing to submit a ground dialog outside the owned MMANA process.")
        button = self.button(dialog, r"OK|确定|確定|确认" if confirm else r"Cancel|取消")
        if button.class_name() != "TButton" or button.parent().handle != dialog.handle:
            raise RuntimeError("Expected the ground dialog's own VCL push button.")
        button.verify_actionable()
        # Target the button's normal parent notification through the public SDK.
        # Synthetic mouse down/up did not dismiss this GAL modal after grid edits.
        # notify_parent posts WM_COMMAND with the actual control ID and HWND, so
        # GAL still runs its button/ModalResult logic; we do not force-close it.
        print(f"[MMANA] Ground setup: {action} via button notification", flush=True)
        if not self.button_wrapper(button.handle).notify_parent(BN_CLICKED):
            raise RuntimeError(f"Could not post the ground dialog's {action} notification.")

        def closed() -> bool:
            if not self.app.is_process_running():
                raise RuntimeError(f"MMANA-GAL exited during ground setup {action}.")
            current = self._dialog()
            if current is not None and current.handle != dialog.handle:
                text = [
                    current.window_text(),
                    *[item.window_text() for item in self.controls(current, r"Static|TLabel")],
                ]
                raise RuntimeError(f"Unexpected dialog during ground setup {action}: {' | '.join(text)}")
            return not dialog.is_visible() and self.main.is_enabled() and current is None

        wait_for(f"Ground setup {action} did not close its dialog and enable the editor", closed, 15)

    def configure_soil(self) -> dict[str, object]:
        # Configure one medium explicitly; never inherit another model's soil or radials.
        dialog = self.open_soil()
        grid, check, radial_edits = self.soil_controls(dialog)
        if self.button_wrapper(check.handle).get_check_state() != 0:
            self.click(check)
        wait_for(
            "Ground screen stayed selected or its radial editors stayed enabled",
            lambda: (
                self.button_wrapper(check.handle).get_check_state() == 0
                and all(not edit.is_enabled() for edit in radial_edits)
            ),
            5,
        )
        # X/R separates media in multi-medium setups. This case has one medium
        # and no ground screen; use GAL's numerical X=0 instead of requiring an
        # empty representation inferred from another MMANA implementation.
        readback: list[dict[str, object]] = []
        for column, (field, expected) in enumerate(SOIL_FIELDS):
            self.send_keys(grid, "^{HOME}" + "{RIGHT}" * column + "{F2}")
            edit = self.ground_editor(dialog)
            previous = edit.window_text().strip()
            self.set_edit(edit, f"{expected:g}")
            self.send_keys(edit, "{ENTER}")
            self.send_keys(grid, "^{HOME}" + "{RIGHT}" * column + "{F2}")
            edit = self.ground_editor(dialog)
            actual = edit.window_text().strip()
            readback.append(
                {"stage": "configure", "field": field, "expected": expected, "before": previous, "actual": actual}
            )
            write_json(self.output / "soil-readback.json", readback)
            if not actual or float(actual) != expected:
                raise RuntimeError(f"Ground {field}: expected {expected:g}, read back {actual!r}.")
            self.send_keys(edit, "{ESC}")
        self.close_soil(dialog, confirm=True)

        # Reopening reads GAL's accepted environment, not merely text left in an editor.
        dialog = self.open_soil()
        grid, check, radial_edits = self.soil_controls(dialog)
        if self.button_wrapper(check.handle).get_check_state() != 0 or any(edit.is_enabled() for edit in radial_edits):
            raise RuntimeError("Ground screen configuration did not persist after OK.")
        actual_values: dict[str, float] = {}
        for column, (field, expected) in enumerate(SOIL_FIELDS):
            self.send_keys(grid, "^{HOME}" + "{RIGHT}" * column + "{F2}")
            edit = self.ground_editor(dialog)
            actual = edit.window_text().strip()
            readback.append({"stage": "reopen", "field": field, "expected": expected, "actual": actual})
            write_json(self.output / "soil-readback.json", readback)
            if not actual or float(actual) != expected:
                raise RuntimeError(f"Ground {field} after OK: expected {expected:g}, read back {actual!r}.")
            actual_values[field] = float(actual)
            self.send_keys(edit, "{ESC}")
        self.send_keys(grid, "^{HOME}{DOWN}{F2}")
        edit = self.ground_editor(dialog)
        if edit.window_text().strip():
            raise RuntimeError("Multiple ground media present; refusing an ambiguous soil case.")
        self.send_keys(edit, "{ESC}")
        self.save_screenshot(dialog, "soil-settings.png")
        ground_screen_caption = caption(check.window_text())
        # This second opening only verifies the saved settings; discard editor activity.
        self.close_soil(dialog, confirm=False)
        return {
            **actual_values,
            "media_count": 1,
            "ground_screen_enabled": False,
            "ground_screen_caption": ground_screen_caption,
        }

    def ground_editor(self, dialog: Any) -> Any:
        def ready() -> Any:
            editors = self.controls(dialog, r"TInplaceEdit|TInplaceEditList")
            return unique(editors, "Ground cell editor") if editors else None

        return wait_for("Ground cell editor did not become visible", ready, 5)

    def log_text(self) -> str:
        return "\n".join(
            item.window_text()
            for item in self.controls(self.main, r"TMemo|TRichEdit|RichEdit.*|Edit")
            if item.window_text()
        )

    def calculate(self, frequency: float) -> str:
        field = self.calculation_frequency()
        edit = unique(self.controls(field, r"TEdit|Edit"), "Calculation frequency edit before Start")
        actual = float(edit.window_text())
        if not math.isclose(actual, frequency, rel_tol=0, abs_tol=1e-6):
            raise RuntimeError(f"Before Start: expected {frequency:g} MHz, read back {actual:g} MHz.")
        if re.search(SUCCESS, self.log_text(), re.IGNORECASE):
            raise RuntimeError("Fresh process already contains successful results; refusing stale capture.")
        self.click(self.button(self.main, START))

        def completed() -> str:
            if not self.app.is_process_running():
                raise RuntimeError("MMANA-GAL exited during calculation.")
            self.require_no_dialog("calculating")
            log = self.log_text()
            if re.search(FAILED, log, re.IGNORECASE):
                raise RuntimeError("MMANA-GAL reported a calculation failure.")
            if re.search(SUCCESS, log, re.IGNORECASE) and self.button(self.main, START).is_enabled():
                return log
            return ""

        log = wait_for("Calculation did not report success within 90 seconds", completed)
        (self.output / "calculation.txt").write_text(log, encoding="utf-8")
        self.save_screenshot(self.main, "result.png")
        return log

    def current(self) -> None:
        path = self.output / "current.csv"
        self.menu(r"(?:电流|電流|Currents?).*\.csv")
        self.file_dialog(path)
        self.wait_export(path)
        numeric_csv(path, 9)

    def pattern(self, case: Case) -> None:
        path = self.output / "pattern.csv"
        self.menu(r"(?:方位及增益|方位.*增益|远场|遠場|Far[ -]?field|Azimuth.*gain).*\.csv")
        dialog = self.dialog()
        # FarSet has two rows of three numeric edits and a separate filename edit.
        # Require that layout rather than absolute desktop coordinates or guessed labels.
        groups = []
        for group in self.controls(dialog, r"TGroupBox|Button"):
            edits = self.controls(group, r"TEdit|Edit")
            if len(edits) == 6:
                groups.append(edits)
        edits = unique(groups, "Far-field angle group with six fields")
        rows: dict[int, list[Any]] = {}
        for edit in edits:
            rows.setdefault(edit.rectangle().top, []).append(edit)
        if len(rows) != 2 or any(len(row) != 3 for row in rows.values()):
            raise RuntimeError("Unrecognized far-field edit layout.")
        ordered = [
            edit for _, row in sorted(rows.items()) for edit in sorted(row, key=lambda item: item.rectangle().left)
        ]
        values = ("0", "5", "72", "0", "5", "19" if case.ground else "37")
        for edit, value in zip(ordered, values, strict=True):
            self.set_edit(edit, value)
        numeric_handles = {edit.handle for edit in edits}
        filename = unique(
            [edit for edit in self.controls(dialog, r"TEdit|Edit") if edit.handle not in numeric_handles],
            "Far-field filename",
        )
        self.set_edit(filename, str(path))
        self.save_screenshot(dialog, "pattern-settings.png")
        self.click(self.button(dialog, r"OK|确定|確定|确认|Start|开始"))
        self.wait_export(path)
        rows_data = numeric_csv(path, 5)
        actual_angles = {(row[0], row[1]) for row in rows_data}
        expected_angles = {
            (float(theta), float(phi)) for theta in range(0, 91 if case.ground else 181, 5) for phi in range(0, 360, 5)
        }
        if actual_angles != expected_angles or len(rows_data) != len(expected_angles):
            raise RuntimeError("Far-field CSV does not match requested zenith/azimuth sampling.")
        write_json(
            self.output / "pattern-metadata.json",
            {
                "coordinates": "GAL CSV zenith, azimuth; degrees",
                "gain_unit": "dBi",
                "columns": ["theta", "phi", "vertical", "horizontal", "total"],
                "angular_step_deg": 5,
                "sample_count": len(rows_data),
                "polarization_mapping": "Native GAL columns retained, not inferred from pictures.",
            },
        )
        self.tab(r"远场图|遠場圖|Far\s+field\s+plots|Far\s+field")
        self.save_screenshot(self.main, "pattern.png")

    def wait_export(self, path: Path) -> None:
        previous = -1
        unchanged = 0

        def finished() -> bool:
            nonlocal previous, unchanged
            if not self.app.is_process_running():
                raise RuntimeError("MMANA-GAL exited during export.")
            size = path.stat().st_size if path.exists() else 0
            unchanged = unchanged + 1 if size > 0 and size == previous else 0
            previous = size
            return unchanged >= 5 and self.main.is_enabled() and not self._dialog()

        wait_for(f"Export did not finish: {path.name}", finished)

    def diagnostic(self) -> None:
        errors = []
        if not getattr(self.app, "process", None):
            return
        windows = self.app.windows(visible_only=False)
        write_json(
            self.output / "windows.json",
            [
                {
                    "class": window.class_name(),
                    "text": window.window_text(),
                    "visible": window.is_visible(),
                    "enabled": window.is_enabled(),
                }
                for window in windows
            ],
        )
        for index, window in enumerate(windows):
            if not self.is_ui_window(window):
                continue
            try:
                rows = []
                for control in [window, *window.descendants()]:
                    rect = control.rectangle()
                    rows.append(
                        {
                            "class": control.class_name(),
                            "text": control.window_text(),
                            "handle": control.handle,
                            "parent_handle": control.parent().handle if control.parent() else None,
                            "control_id": control.control_id(),
                            "visible": control.is_visible(),
                            "enabled": control.is_enabled(),
                            "rect": [rect.left, rect.top, rect.right, rect.bottom],
                        }
                    )
                write_json(self.output / f"window-{index}-controls.json", rows)
                self.save_screenshot(window, f"window-{index}.png")
            except Exception as exc:
                errors.append(f"window {index}: {type(exc).__name__}: {exc}")
        if self.main is not None:
            try:
                if self.main.is_enabled():
                    write_json(self.output / "menus.json", [path for path, _ in self.menus()])
            except Exception as exc:
                errors.append(f"menus: {type(exc).__name__}: {exc}")
        if errors:
            write_json(self.output / "diagnostic-errors.json", errors)

    def close(self) -> None:
        if getattr(self.app, "process", None) and self.app.is_process_running():
            # Only this object's child is owned. Do not taskkill by executable name.
            self.app.kill(soft=False)
            self.app.wait_for_process_exit(timeout=15)


def numeric_csv(path: Path, columns: int) -> list[list[float]]:
    # Keep original CSV bytes. Inspect numeric rows without rewriting ANSI headers.
    text = path.read_bytes().decode("utf-8", errors="replace")
    result = []
    for row in csv.reader(text.splitlines()):
        if not row or not re.match(r"^\s*[-+]?\d", row[0]):
            continue
        # GAL 1.2.0.20 far-field exports include a trailing comma after total gain.
        while row and not row[-1].strip():
            row.pop()
        if len(row) != columns:
            raise ValueError(f"{path.name}: expected {columns} columns, got {len(row)}")
        values = [float(value) for value in row]
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"{path.name}: non-finite numerical value")
        result.append(values)
    if not result:
        raise ValueError(f"{path.name}: no numerical rows")
    return result


def capture_case(
    exe: Path,
    output: Path,
    case: Case,
    frequency: float,
    inspect: bool,
    full: bool,
) -> dict[str, object]:
    output.mkdir()
    source = output / "input.maa"
    source.write_bytes(case.maa(frequency).encode("ascii"))
    runner = Capture(exe, output)
    record: dict[str, object] = {"case": case.name, "frequency_mhz": frequency, "status": "STARTED"}

    def stage(name: str) -> None:
        # Record the failing operation as well as its traceback in the returned bundle.
        record["stage"] = name
        print(f"[MMANA] {case.name}: {name}", flush=True)

    try:
        stage("open_model")
        runner.start(source)
        if inspect:
            record["status"] = "INSPECTED_ONLY"
        else:
            stage("configure")
            record["settings"] = runner.configure(case, frequency)
            saved = output / "model.maa"
            stage("save_model")
            runner.save_model(saved)
            stage("verify_model")
            check_saved_model(saved, case, frequency)
            stage("calculate")
            runner.calculate(frequency)
            stage("export_current")
            runner.current()
            if full:
                stage("export_pattern")
                runner.pattern(case)
            record["status"] = "CAPTURED_REVIEW_REQUIRED"
    except KeyboardInterrupt:
        record["status"] = "CANCELLED"
    except Exception:
        record["status"] = "FAILED"
        record["error"] = traceback.format_exc()
        print(f"[MMANA] {case.name} failed:\n{record['error']}", file=sys.stderr, flush=True)
    finally:
        record["owned_process_id"] = getattr(runner.app, "process", None)
        try:
            try:
                runner.diagnostic()
            except KeyboardInterrupt:
                record["status"] = "CANCELLED"
            except Exception:
                record["diagnostic_error"] = traceback.format_exc()
        finally:
            try:
                runner.close()
            except BaseException:
                # Retain the work directory if cleanup itself was interrupted; do
                # not report success or delete a program that might still be running.
                record["cleanup_error"] = traceback.format_exc()
                record["status"] = "CLEANUP_FAILED"
            write_json(output / "capture.json", record)
    return record


def finish_capture(work: Path, output: Path, report: dict[str, object], records: list[dict[str, object]]) -> bool:
    """Preserve results before removing the owned staging directory; always attempt the ZIP."""
    report["cases"] = records
    report["work_directory_retained"] = str(work)
    complete = True
    try:
        data = work / "capture"
        if data.exists():
            shutil.copytree(data, output / "cases")
        # Persist the original failure and copied evidence before attempting cleanup.
        write_json(output / "run.json", report)
    except (OSError, shutil.Error):
        complete = False
        report["preservation_error"] = traceback.format_exc()
        print(
            f"[MMANA] Could not preserve all results; keeping {work}\n{report['preservation_error']}",
            file=sys.stderr,
        )
    else:
        if any(record.get("cleanup_error") for record in records):
            complete = False
        else:
            try:
                shutil.rmtree(work)
            except OSError:
                # Even after GAL exits, Windows can retain a directory handle.
                # Do not kill unrelated processes or let this hide the capture error.
                complete = False
                report["directory_cleanup_error"] = traceback.format_exc()
                print(f"[MMANA] Temporary directory still in use or not removable; keeping {work}", file=sys.stderr)
            else:
                report.pop("work_directory_retained")
    report["finalization_status"] = "COMPLETE" if complete else "INCOMPLETE"
    if "work_directory_retained" in report:
        print(f"[MMANA] Retained temporary directory: {work}", flush=True)
    archive = output.with_suffix(".zip")
    try:
        write_json(output / "run.json", report)
        manifest = {str(path.relative_to(output)): sha256(path) for path in output.rglob("*") if path.is_file()}
        write_json(output / "manifest.json", manifest)
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in output.rglob("*"):
                if path.is_file():
                    bundle.write(path, path.relative_to(output))
    except (OSError, ValueError, zipfile.LargeZipFile):
        print(f"[MMANA] ZIP creation failed; return the result directory instead: {output}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        return False
    print(f"[MMANA] Return this bundle: {archive}", flush=True)
    return complete


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path)
    parser.add_argument("--output-root", type=Path, default=Path(__file__).parent / "results")
    parser.add_argument("--inspect-only", action="store_true")
    parser.add_argument(
        "--case", choices=[case.name for case in ALL_CASES], help="Capture only this case into a new bundle."
    )
    parser.add_argument("--sweep", action="store_true", help="Add 12 frequencies per base case; no optimizer or LLM.")
    parser.add_argument("--next-batch", action="store_true", help="3 geometries + 20 missing sweep points; 23 runs.")
    parser.add_argument("--list-plan", action="store_true", help="Print the fixed jobs without starting Windows GUI.")
    args = parser.parse_args()
    if args.inspect_only and (args.sweep or args.next_batch):
        parser.error("--inspect-only cannot be combined with a numerical batch.")
    try:
        jobs = capture_plan(next_batch=args.next_batch, sweep=args.sweep, case_name=args.case)
    except ValueError as exc:
        parser.error(str(exc))
    if args.inspect_only:
        jobs = jobs[:1]
    plan = [
        {"name": job.name, "case": job.case.name, "frequency_mhz": job.frequency_mhz, "pattern": job.full_pattern}
        for job in jobs
    ]
    if args.list_plan:
        print(json.dumps({"capture_count": len(jobs), "jobs": plan}, indent=2))
        return 0
    if sys.platform != "win32":
        parser.error("Run this script on the Windows desktop containing MMANA-GAL.")
    if args.exe is None:
        parser.error("--exe is required for GUI capture.")
    import win32api
    from pywinauto import Application
    from pywinauto.application import ProcessNotFoundError

    exe = args.exe.resolve(strict=True)
    try:
        Application().connect(path=str(exe), timeout=1)
    except ProcessNotFoundError:
        pass
    else:
        parser.error("Close your existing MMANA-GAL window first; it will not be touched or killed.")
    run_id = datetime.now(timezone.utc).strftime("mmana-%Y%m%dT%H%M%SZ")
    args.output_root.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix=run_id + "-", dir=args.output_root)).resolve()
    work = Path(tempfile.mkdtemp(prefix="mmana-capture-"))
    report: dict[str, object] = {
        "status": "STARTED",
        "platform": platform.platform(),
        "python": sys.version,
        "claimed_reference_version": "1.2.0.20",
        "cases": [],
        "verification": "GUI capture only, not compatibility PASS. Review settings and original exports.",
    }
    records: list[dict[str, object]] = []
    report["requested_cases"] = list(dict.fromkeys(job.case.name for job in jobs))
    report["capture_plan"] = plan
    report["batch"] = "NEXT_23" if args.next_batch else "BASE_OR_SELECTED"
    exit_code = 1
    try:
        report["exe_sha256"] = sha256(exe)
        report["script_sha256"] = sha256(Path(__file__))
        report["recipes_sha256"] = sha256(Path(__file__).with_name("recipes.py"))
        print(f"[MMANA] Script SHA256: {report['script_sha256']}", flush=True)
        print(f"[MMANA] Capture plan: {len(jobs)} runs; no existing result is overwritten.", flush=True)
        report["dependencies"] = {
            name: importlib.metadata.version(name) for name in ("pywinauto", "Pillow", "pywin32", "comtypes")
        }
        version = win32api.GetFileVersionInfo(str(exe), "\\")
        ms, ls = version["FileVersionMS"], version["FileVersionLS"]
        report["actual_exe_version"] = f"{ms >> 16}.{ms & 65535}.{ls >> 16}.{ls & 65535}"
        # GAL builds may use ANSI paths internally; refuse unrepresentable filenames.
        str(work).encode("mbcs", errors="strict")
        staged = installation_copy(exe, work / "program")
        str(staged).encode("mbcs", errors="strict")
        report["staged_exe_sha256"] = sha256(staged)
        data = work / "capture"
        data.mkdir()
        for index, job in enumerate(jobs, start=1):
            print(f"[MMANA] {index}/{len(jobs)} {job.name}", flush=True)
            record = capture_case(
                staged, data / job.name, job.case, job.frequency_mhz, args.inspect_only, job.full_pattern
            )
            record["capture_name"] = job.name
            records.append(record)
            print(f"[MMANA] {record['status']}", flush=True)
            if record["status"] == "CANCELLED":
                raise KeyboardInterrupt
            if record["status"] in {"FAILED", "CLEANUP_FAILED"}:
                raise RuntimeError(f"Stopped at {job.name}; see capture.json and window diagnostics.")
        report["status"] = "INSPECTED_ONLY" if args.inspect_only else "CAPTURED_REVIEW_REQUIRED"
        exit_code = 0
    except KeyboardInterrupt:
        report["status"] = "CANCELLED"
        exit_code = 130
    except Exception:
        report["status"] = "FAILED"
        report["error"] = traceback.format_exc()
        print(report["error"], file=sys.stderr)
    finally:
        if not finish_capture(work, output, report, records) and exit_code == 0:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
