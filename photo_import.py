import ctypes
import filecmp
import os
import re
import shutil
import string
import tempfile
import tkinter as tk
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, font, messagebox, ttk


def drives():
    api = ctypes.windll.kernel32
    mask = api.GetLogicalDrives()
    for bit, letter in enumerate(string.ascii_uppercase):
        root = letter + ":\\"
        if not mask & (1 << bit) or api.GetDriveTypeW(root) not in (2, 3):
            continue
        label = ctypes.create_unicode_buffer(261)
        serial = ctypes.c_ulong()
        if api.GetVolumeInformationW(root, label, len(label), ctypes.byref(serial),
                                     None, None, None, 0):
            yield Path(root), label.value, serial.value


def desktop():
    path = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 16, None, 0, path) != 0:
        raise OSError("Не удалось найти рабочий стол Windows.")
    return Path(path.value) / "Фото с устройства"


def source_folders(root):
    dcim = root / "DCIM"
    if not dcim.is_dir():
        return [root]
    return [dcim, *sorted(
        (path for path in dcim.rglob("*") if path.is_dir()),
        key=lambda path: str(path.relative_to(dcim)).casefold(),
    )]


@dataclass(frozen=True)
class File:
    path: Path
    modified: datetime


def scan_folders(folders):
    files = {}
    errors = []
    for folder in folders:
        try:
            for path in folder.iterdir():
                try:
                    if path.is_file():
                        info = path.stat()
                        files[path] = File(
                            path, datetime.fromtimestamp(info.st_mtime)
                        )
                except OSError as error:
                    errors.append(str(error))
        except OSError as error:
            errors.append(str(error))
    return sorted(files.values(), key=lambda file: file.modified, reverse=True), errors


def date_range(first, last):
    if first == last:
        return first.strftime("%d.%m.%y")
    if (first.year, first.month) == (last.year, last.month):
        return f"{first:%d}-{last:%d.%m.%y}"
    return f"{first:%d.%m.%y}–{last:%d.%m.%y}"


def import_plan(files):
    return [(file.modified.strftime("%d.%m.%y"), file.path) for file in files]


def dated_folders(destination):
    if not destination.is_dir():
        return []
    folders = []
    for folder in destination.iterdir():
        if not re.fullmatch(r"[0-9]{2}\.[0-9]{2}\.[0-9]{2}", folder.name):
            continue
        if not folder.is_dir() or folder.is_symlink():
            continue
        try:
            day = datetime.strptime(folder.name, "%d.%m.%y").date()
        except ValueError:
            continue
        folders.append((day, folder))
    return sorted(folders)


def copy_files(destination, selection):
    copied = skipped = 0
    errors = []
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination) as work:
        temporary = Path(work) / "copy"
        for day, source in selection:
            folder = destination / day
            try:
                target = folder / source.name
                number = 1
                while target.exists():
                    if target.is_file() and filecmp.cmp(source, target, shallow=False):
                        skipped += 1
                        break
                    number += 1
                    target = folder / f"{source.stem} ({number}){source.suffix}"
                else:
                    shutil.copy2(source, temporary)
                    folder.mkdir(exist_ok=True)
                    try:
                        temporary.rename(target)
                    except OSError:
                        try:
                            folder.rmdir()
                        except OSError:
                            pass
                        raise
                    copied += 1
            except OSError as error:
                errors.append(f"{source.name}: {error}")
    return copied, skipped, errors


class PhotoImport:
    def __init__(self, window):
        self.window = window
        self.manual = []
        self.folders = []
        self.groups = {}
        self.imported = []
        self.output_key = None
        self.disk_key = None
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.future = None
        self.disk = tk.StringVar(value="E:")
        self.status = tk.StringVar()
        self.summary = tk.StringVar()
        self.merge_preview = tk.StringVar()
        window.title("Импорт файлов")
        window.geometry("1120x570")
        window.minsize(900, 420)
        window.protocol("WM_DELETE_WINDOW", self.close)
        self.layout()
        self.refresh()
        self.refresh_imported()
        self.watch()

    def layout(self):
        style = ttk.Style()
        style.theme_use("clam")
        family = "Segoe UI" if "Segoe UI" in font.families() else "DejaVu Sans"
        bg, panel, edge = "#20252c", "#171c22", "#46515d"
        text, muted, accent = "#edf2f8", "#b1bfce", "#205c9b"
        self.window.configure(background=bg)
        style.configure(".", font=(family, 10), background=bg, foreground=text,
                        bordercolor=edge, lightcolor=edge, darkcolor=edge, arrowcolor=text)
        style.configure("Title.TLabel", font=(family, 15, "bold"))
        style.configure("Section.TLabel", font=(family, 10, "bold"))
        style.configure("Muted.TLabel", foreground=muted)
        style.configure("TButton", padding=(9, 5), background="#303943")
        style.map("TButton", background=[("disabled", "#252d36"), ("active", "#3b4651")],
                  foreground=[("disabled", "#8392a0")])
        style.configure("Accent.TButton", background=accent)
        style.map("Accent.TButton", background=[
            ("disabled", "#252d36"), ("pressed", "#204e7e"), ("active", "#286cab")
        ])
        style.configure("TCombobox", fieldbackground=panel, background="#303943",
                        foreground=text, insertcolor=text, selectbackground=accent)
        style.map("TCombobox", fieldbackground=[("readonly", panel)])
        self.window.option_add("*TCombobox*Listbox.background", panel)
        self.window.option_add("*TCombobox*Listbox.foreground", text)
        self.window.option_add("*TCombobox*Listbox.selectBackground", accent)
        style.configure("Treeview", background=panel, fieldbackground=panel,
                        rowheight=24, borderwidth=1)
        style.configure("Treeview.Heading", background="#303943", padding=(6, 4),
                        font=(family, 9, "bold"))
        style.map("Treeview", background=[("selected", accent)],
                  foreground=[("selected", "#ffffff")])
        style.map("Treeview.Heading", background=[("active", "#394652")])
        style.configure("Vertical.TScrollbar", troughcolor=panel, background="#303943")
        style.map("Vertical.TScrollbar", background=[("active", "#46515d")])
        style.configure("Horizontal.TProgressbar", troughcolor=panel, background="#428bd5")

        body = ttk.Frame(self.window, padding=(12, 8))
        body.pack(fill="both", expand=True)
        controls = ttk.Frame(body)
        controls.pack(fill="x", pady=(0, 8))
        ttk.Label(controls, text="Импорт файлов", style="Title.TLabel").pack(side="left", padx=(0, 20))
        ttk.Label(controls, text="Диск").pack(side="left")
        self.disk_input = ttk.Combobox(controls, textvariable=self.disk, width=16)
        self.disk_input.pack(side="left", padx=6)
        self.disk_input.bind("<<ComboboxSelected>>", lambda _: self.refresh())
        self.disk_input.bind("<Return>", lambda _: self.refresh())
        self.refresh_button = ttk.Button(controls, text="Обновить", command=self.refresh_all)
        self.refresh_button.pack(side="left")
        self.add_button = ttk.Button(controls, text="Добавить папку…", command=self.add_folder)
        self.add_button.pack(side="left", padx=6)
        ttk.Button(controls, text="Открыть папку импорта", command=self.open_output).pack(side="right")
        ttk.Separator(body).pack(fill="x", pady=(0, 8))

        panes = ttk.Panedwindow(body, orient="horizontal")
        panes.pack(fill="both", expand=True)
        sources = ttk.Frame(panes, padding=(0, 0, 6, 0))
        dates = ttk.Frame(panes, padding=(6, 0))
        imported = ttk.Frame(panes, padding=(6, 0, 0, 0))
        for pane, title, weight in (
            (sources, "Исходные папки", 2), (dates, "Файлы по датам", 1),
            (imported, "Импортированные папки", 2),
        ):
            panes.add(pane, weight=weight)
            ttk.Label(pane, text=title, style="Section.TLabel").pack(anchor="w", pady=(0, 6))

        self.folder_list = self.tree(sources, show="tree headings", selectmode="extended")
        self.folder_list.heading("#0", text="Папка", anchor="w")
        self.folder_list.column("#0", width=300, minwidth=150)
        self.folder_list.bind("<<TreeviewSelect>>", lambda _: self.load_files())
        self.all_button = ttk.Button(sources, text="Все папки", command=self.select_all)
        self.all_button.pack(anchor="w", pady=(6, 0))

        self.date_list = self.tree(dates, columns=("date", "count"),
                                   show="headings", selectmode="extended")
        self.date_list.heading("date", text="Дата", anchor="w")
        self.date_list.heading("count", text="Файлов", anchor="e")
        self.date_list.column("date", width=125, minwidth=90)
        self.date_list.column("count", width=73, minwidth=55, anchor="e", stretch=False)
        self.date_list.bind("<<TreeviewSelect>>", lambda _: self.update_import())
        self.all_dates_button = ttk.Button(
            dates, text="Все даты", command=lambda: self.date_list.selection_set(self.date_list.get_children())
        )
        self.all_dates_button.pack(side="left", pady=(6, 0))
        self.copy_button = ttk.Button(dates, text="Импортировать",
                                      style="Accent.TButton", command=self.import_selected)
        self.copy_button.pack(side="right", pady=(6, 0))

        self.imported_list = self.tree(imported, columns=("folder", "count"),
                                       show="headings", selectmode="extended")
        self.imported_list.heading("folder", text="Папка", anchor="w")
        self.imported_list.heading("count", text="Файлов", anchor="e")
        self.imported_list.column("folder", width=190, minwidth=105)
        self.imported_list.column("count", width=73, minwidth=55, anchor="e", stretch=False)
        self.imported_list.bind("<<TreeviewSelect>>", lambda _: self.update_merge())
        self.merge_button = ttk.Button(imported, text="Объединить выбранные",
                                       style="Accent.TButton", command=self.merge_selected)
        self.merge_button.pack(side="right", pady=(6, 0))
        ttk.Label(imported, textvariable=self.merge_preview, style="Muted.TLabel").pack(
            side="left", pady=(6, 0)
        )

        footer = ttk.Frame(body)
        footer.pack(fill="x", pady=(9, 0))
        ttk.Label(footer, textvariable=self.status, style="Muted.TLabel").pack(side="left")
        ttk.Label(footer, textvariable=self.summary, style="Muted.TLabel").pack(side="right")
        self.progress = ttk.Progressbar(body, mode="determinate")
        self.progress.pack(fill="x", pady=(5, 0))

    @staticmethod
    def tree(parent, **options):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, height=10, **options)
        tree.tag_configure("alternate", background="#1e252d")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)
        return tree

    def refresh(self, force=True):
        if self.future is not None:
            return
        wanted = self.disk.get().strip().casefold()
        attached = list(drives())
        menu = [(path, label, serial, f"{path.drive} — {label}" if label else path.drive)
                for path, label, serial in attached]
        self.disk_input.configure(values=[item[3] for item in menu])
        matches = [(path, serial) for path, label, serial, option in menu
                   if wanted and wanted in (path.drive.casefold(), label.casefold(), option.casefold())]
        key = (wanted, tuple(matches))
        if not force and key == self.disk_key:
            return
        self.disk_key = key

        previous = self.selected_folders()
        old_folders = set(self.folders)
        device = []
        if len(matches) == 1:
            try:
                device = source_folders(matches[0][0])
                self.status.set(str(matches[0][0]))
            except OSError as error:
                self.status.set(f"Диск недоступен: {error}")
        elif len(matches) > 1:
            self.status.set("У нескольких дисков одинаковая метка. Укажите букву.")
        else:
            self.status.set("Подключите диск или добавьте папку вручную.")

        self.folders = list(dict.fromkeys(device + self.manual))
        self.folder_list.delete(*self.folder_list.get_children())
        selection = []
        for index, folder in enumerate(self.folders):
            self.folder_list.insert("", "end", iid=str(index), text=str(folder),
                                    tags=("alternate",) if index % 2 else ())
            if folder in previous or folder not in old_folders:
                selection.append(str(index))
        self.folder_list.selection_set(selection)
        self.load_files()

    def refresh_all(self):
        self.refresh()
        self.refresh_imported()

    def watch(self):
        self.refresh(False)
        self.refresh_imported(False)
        self.window.after(1500, self.watch)

    def selected_folders(self):
        return [self.folders[int(index)] for index in self.folder_list.selection()]

    def select_all(self):
        self.folder_list.selection_set(self.folder_list.get_children())

    def add_folder(self):
        folder = filedialog.askdirectory(title="Выберите папку с файлами")
        if folder:
            path = Path(folder)
            if path not in self.manual:
                self.manual.append(path)
            self.refresh()
            self.folder_list.selection_add(str(self.folders.index(path)))

    def load_files(self):
        if self.future is not None:
            return
        old_rows = set(self.date_list.get_children())
        old_selection = set(self.date_list.selection())
        files, errors = scan_folders(self.selected_folders())
        self.groups = defaultdict(list)
        for file in files:
            self.groups[file.modified.date().isoformat()].append(file)
        self.date_list.delete(*old_rows)
        for index, (key, group) in enumerate(self.groups.items()):
            day = group[0].modified.strftime("%d.%m.%y")
            self.date_list.insert("", "end", iid=key, values=(day, len(group)),
                                  tags=("alternate",) if index % 2 else ())
        self.date_list.selection_set([
            key for key in self.groups if key not in old_rows or key in old_selection
        ])
        if errors:
            self.status.set(f"Не удалось прочитать часть файлов или папок: {len(errors)}")
        self.update_import()

    def update_import(self):
        if self.future is not None:
            return
        count = sum(len(self.groups[key]) for key in self.date_list.selection())
        self.summary.set(f"К импорту: {count} файлов")
        self.copy_button.configure(state="normal" if count else "disabled")

    def import_selected(self):
        files = [file for key in self.date_list.selection() for file in self.groups[key]]
        self.start_copy(import_plan(files))

    def refresh_imported(self, force=True):
        if self.future is not None:
            return
        try:
            destination = desktop()
            stamp = destination.stat().st_mtime_ns if destination.exists() else None
            key = (destination, stamp)
            if not force and key == self.output_key:
                return
            folders = dated_folders(destination)
        except OSError as error:
            self.status.set(str(error))
            return
        self.output_key = key
        selected = set(self.imported_list.selection())
        self.imported_list.delete(*self.imported_list.get_children())
        self.imported = {folder.name: (day, folder) for day, folder in folders}
        for index, (_, folder) in enumerate(folders):
            try:
                count = sum(path.is_file() for path in folder.iterdir())
            except OSError:
                count = "—"
            self.imported_list.insert("", "end", iid=folder.name, values=(folder.name, count),
                                      tags=("alternate",) if index % 2 else ())
        self.imported_list.selection_set(tuple(selected.intersection(self.imported)))
        self.update_merge()

    def selected_imported(self):
        return sorted(self.imported[key] for key in self.imported_list.selection())

    def update_merge(self):
        if self.future is not None:
            return
        selected = self.selected_imported()
        enabled = len(selected) >= 2
        self.merge_button.configure(state="normal" if enabled else "disabled")
        self.merge_preview.set(
            date_range(selected[0][0], selected[-1][0]) if enabled else "Ctrl — несколько папок"
        )

    def open_output(self):
        try:
            output = desktop()
            if output.is_dir():
                os.startfile(output)
            else:
                self.status.set("Папка импорта появится после первого копирования.")
        except OSError as error:
            self.status.set(str(error))

    def merge_selected(self):
        selected = self.selected_imported()
        if len(selected) < 2 or self.future is not None:
            return
        combined = date_range(selected[0][0], selected[-1][0])
        files, errors = scan_folders([folder for _, folder in selected])
        if errors:
            messagebox.showerror("Не удалось прочитать папки", "\n".join(errors[:8]),
                                 parent=self.window)
            return
        if not files:
            self.status.set("В выбранных папках нет файлов.")
            return
        self.start_copy([(combined, file.path) for file in files], combined)

    def start_copy(self, plan, merged_name=None):
        if not plan or self.future is not None:
            return
        for control in (self.copy_button, self.merge_button, self.disk_input, self.refresh_button,
                        self.add_button, self.all_button, self.all_dates_button):
            control.configure(state="disabled")
        for tree in (self.folder_list, self.date_list, self.imported_list):
            tree.state(["disabled"])
        self.status.set(f"Копирование: {len(plan)} файлов…")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.future = self.worker.submit(lambda: copy_files(desktop(), plan))
        self.window.after(150, lambda: self.finish(merged_name))

    def finish(self, merged_name):
        if not self.future.done():
            self.window.after(150, lambda: self.finish(merged_name))
            return
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        for control in (self.disk_input, self.refresh_button, self.add_button, self.all_button, self.all_dates_button):
            control.configure(state="normal")
        for tree in (self.folder_list, self.date_list, self.imported_list):
            tree.state(["!disabled"])
        future, self.future = self.future, None
        self.load_files()
        self.refresh_imported()
        try:
            copied, skipped, errors = future.result()
            prefix = f"{merged_name} · " if merged_name and (copied or skipped) else ""
            self.status.set(f"{prefix}Скопировано: {copied}. Уже были: {skipped}. Ошибок: {len(errors)}.")
            if errors:
                messagebox.showwarning("Ошибки копирования", "\n".join(errors[:8]))
        except OSError as error:
            self.status.set(str(error))

    def close(self):
        if self.future is not None:
            messagebox.showinfo("Импорт", "Дождитесь окончания копирования.")
            return
        self.worker.shutdown(wait=False)
        self.window.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    PhotoImport(root)
    root.mainloop()
