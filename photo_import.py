import ctypes
import filecmp
import os
import re
import shutil
import string
import tempfile
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


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
    folder: Path
    modified: datetime
    size: int


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
                            path, folder, datetime.fromtimestamp(info.st_mtime), info.st_size
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


def import_plan(files, merge):
    if not files:
        return []
    dates = [file.modified.date() for file in files]
    combined = date_range(min(dates), max(dates)) if merge else None
    return [
        (combined or day.strftime("%d.%m.%y"), file.path)
        for file, day in zip(files, dates)
    ]


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
        self.files = []
        self.visible = []
        self.disk_key = None
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.future = None
        self.disk = tk.StringVar(value="E:")
        self.date_filter = tk.StringVar(value="Все даты")
        self.status = tk.StringVar()
        self.summary = tk.StringVar()
        window.title("Импорт файлов")
        window.geometry("1080x650")
        window.minsize(840, 520)
        window.protocol("WM_DELETE_WINDOW", self.close)
        self.layout()
        self.refresh()
        self.watch()

    def layout(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), background="#f0f4f8", foreground="#172c43")
        style.configure("Title.TLabel", font=("Segoe UI", 23, "bold"))
        style.configure("Section.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("Muted.TLabel", foreground="#4a6178")
        style.configure("TButton", padding=(12, 7))
        style.configure("Accent.TButton", background="#136d66", foreground="white")
        style.map("Accent.TButton", background=[
            ("disabled", "#d8e2e9"), ("pressed", "#10534e"), ("active", "#0e5c55")
        ], foreground=[("disabled", "#526477")])
        style.configure("Treeview", background="white", fieldbackground="white", rowheight=30)
        style.configure("Treeview.Heading", background="#e4ecf4", padding=(8, 6),
                        font=("Segoe UI", 10, "bold"))
        style.map("Treeview", background=[("selected", "#136d66")],
                  foreground=[("selected", "white")])

        body = ttk.Frame(self.window, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Импорт файлов", style="Title.TLabel").pack(anchor="w")
        controls = ttk.Frame(body)
        controls.pack(fill="x", pady=(12, 18))
        ttk.Label(controls, text="Диск").pack(side="left")
        self.disk_input = ttk.Combobox(controls, textvariable=self.disk, width=19)
        self.disk_input.pack(side="left", padx=(8, 10))
        self.disk_input.bind("<<ComboboxSelected>>", lambda _: self.refresh())
        self.disk_input.bind("<Return>", lambda _: self.refresh())
        self.refresh_button = ttk.Button(controls, text="Обновить", command=self.refresh)
        self.refresh_button.pack(side="left")
        self.add_button = ttk.Button(controls, text="Добавить папку…", command=self.add_folder)
        self.add_button.pack(side="left", padx=8)
        self.output_button = ttk.Button(controls, text="Открыть папку импорта", command=self.open_output)
        self.output_button.pack(side="right")

        panes = ttk.Panedwindow(body, orient="horizontal")
        panes.pack(fill="both", expand=True)
        sidebar = ttk.Frame(panes, padding=(0, 0, 12, 0))
        table = ttk.Frame(panes, padding=(12, 0, 0, 0))
        panes.add(sidebar, weight=1)
        panes.add(table, weight=3)
        ttk.Label(sidebar, text="Папки", style="Section.TLabel").pack(anchor="w")
        ttk.Label(sidebar, text="Ctrl — выбрать несколько папок", style="Muted.TLabel").pack(
            anchor="w", pady=(4, 10)
        )
        self.folder_list = self.tree(sidebar, show="tree", selectmode="extended")
        self.folder_list.column("#0", width=235, minwidth=145)
        self.folder_list.bind("<<TreeviewSelect>>", lambda _: self.load_files())
        self.all_button = ttk.Button(sidebar, text="Выбрать все папки", command=self.select_all)
        self.all_button.pack(anchor="w", pady=(8, 0))

        header = ttk.Frame(table)
        header.pack(fill="x", pady=(0, 9))
        ttk.Label(header, text="Файлы", style="Section.TLabel").pack(side="left")
        self.filter_box = ttk.Combobox(header, textvariable=self.date_filter, width=13,
                                       state="readonly")
        self.filter_box.pack(side="right")
        self.filter_box.bind("<<ComboboxSelected>>", lambda _: self.render_files())
        ttk.Label(header, text="Дата изменения").pack(side="right", padx=8)
        self.file_list = self.tree(table, columns=("name", "folder", "date", "size"),
                                   show="headings", selectmode="browse")
        for key, title, width in (
            ("name", "Имя файла", 240), ("folder", "Папка", 125),
            ("date", "Изменён", 132), ("size", "Размер", 91),
        ):
            self.file_list.heading(key, text=title)
            self.file_list.column(key, width=width, minwidth=65,
                                  stretch=key in ("name", "folder"),
                                  anchor="e" if key == "size" else "w")
        self.file_list.tag_configure("alternate", background="#f3f7fb")
        ttk.Label(table, textvariable=self.summary, style="Muted.TLabel").pack(anchor="w", pady=6)

        footer = ttk.Frame(body)
        footer.pack(fill="x", pady=(14, 0))
        self.merge_button = ttk.Button(footer, text="Объединить папки",
                                       command=lambda: self.start_copy(True))
        self.merge_button.pack(side="right")
        self.copy_button = ttk.Button(footer, text="Импортировать по датам",
                                      style="Accent.TButton",
                                      command=lambda: self.start_copy(False))
        self.copy_button.pack(side="right", padx=(12, 8))
        ttk.Label(footer, textvariable=self.status, wraplength=370,
                  style="Muted.TLabel").pack(side="left", fill="x", expand=True)
        self.progress = ttk.Progressbar(body, mode="indeterminate")
        self.progress.pack(fill="x", pady=(8, 0))

    @staticmethod
    def tree(parent, **options):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, height=12, **options)
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
            self.folder_list.insert("", "end", iid=str(index), text=str(folder))
            if folder in previous or folder not in old_folders:
                selection.append(str(index))
        self.folder_list.selection_set(selection)
        self.load_files()

    def watch(self):
        self.refresh(False)
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
        self.files, errors = scan_folders(self.selected_folders())
        dates = sorted({file.modified.date() for file in self.files}, reverse=True)
        options = ["Все даты", *(day.strftime("%d.%m.%y") for day in dates)]
        self.filter_box.configure(values=options)
        if self.date_filter.get() not in options:
            self.date_filter.set(options[0])
        if errors:
            self.status.set(f"Не удалось прочитать часть файлов или папок: {len(errors)}")
        self.render_files()

    def render_files(self):
        selected = self.date_filter.get()
        self.visible = [file for file in self.files if selected == "Все даты"
                        or file.modified.strftime("%d.%m.%y") == selected]
        self.file_list.delete(*self.file_list.get_children())
        for index, file in enumerate(self.visible):
            size = f"{file.size:,}".replace(",", " ") + " Б"
            self.file_list.insert("", "end", values=(
                file.path.name, file.folder.name or str(file.folder),
                file.modified.strftime("%d.%m.%y %H:%M"), size
            ), tags=("alternate",) if index % 2 else ())
        sources = {file.folder for file in self.visible}
        plan = import_plan(self.visible, True) if len(sources) >= 2 else []
        self.summary.set(f"Файлов: {len(self.visible)}" +
                         (f"  ·  Папка при объединении: {plan[0][0]}" if plan else ""))
        self.copy_button.configure(state="normal" if self.visible else "disabled")
        self.merge_button.configure(state="normal" if plan else "disabled")

    def open_output(self):
        try:
            output = desktop()
            if output.is_dir():
                os.startfile(output)
            else:
                self.status.set("Папка импорта появится после первого копирования.")
        except OSError as error:
            self.status.set(str(error))

    def start_copy(self, merge):
        plan = import_plan(self.visible, merge)
        if not plan or self.future is not None:
            return
        for control in (self.copy_button, self.merge_button, self.disk_input, self.refresh_button,
                        self.add_button, self.filter_box, self.all_button):
            control.configure(state="disabled")
        self.folder_list.state(["disabled"])
        self.status.set(f"Копирование: {len(plan)} файлов…")
        self.progress.start(12)
        self.future = self.worker.submit(lambda: copy_files(desktop(), plan))
        self.window.after(150, lambda: self.finish(plan[0][0] if merge else None))

    def finish(self, merged_name):
        if not self.future.done():
            self.window.after(150, lambda: self.finish(merged_name))
            return
        self.progress.stop()
        for control in (self.disk_input, self.refresh_button, self.add_button, self.all_button):
            control.configure(state="normal")
        self.filter_box.configure(state="readonly")
        self.folder_list.state(["!disabled"])
        future, self.future = self.future, None
        self.load_files()
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
