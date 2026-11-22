import ctypes
import filecmp
import re
import shutil
import string
import tempfile
import tkinter as tk
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk


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


def scan(root, minimum):
    folders = []
    for path in (root / "DCIM").iterdir():
        match = re.fullmatch(r"-?([0-9]+)-?PHOTO", path.name, re.IGNORECASE)
        if match and path.is_dir() and int(match[1]) >= minimum:
            folders.append((int(match[1]), path))
    if not folders:
        raise ValueError(f"В DCIM нет папки PHOTO с номером от {minimum}.")
    _, folder = max(folders)
    groups = defaultdict(list)
    for path in sorted(folder.iterdir()):
        if path.is_file():
            day = datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
            groups[day].append(path)
    return folder, groups


def display_date(day):
    return datetime.fromisoformat(day).strftime("%d.%m.%y")


def copy_files(destination, selection):
    copied = skipped = 0
    errors = []
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination) as work:
        temporary = Path(work) / "copy"
        for day, source in selection:
            folder = destination / display_date(day)
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


def main():
    window = tk.Tk()
    window.title("Импорт файлов с устройства")
    window.geometry("640x420")
    window.minsize(480, 360)
    panel = ttk.Frame(window, padding=16)
    panel.pack(fill="both", expand=True)
    settings = ttk.Frame(panel)
    settings.pack(fill="x")
    ttk.Label(settings, text="Буква или метка диска").grid(row=0, column=0, sticky="w")
    label = ttk.Combobox(settings, width=25)
    label.set("E:")
    label.grid(row=0, column=1, padx=8, pady=4, sticky="w")
    ttk.Label(settings, text="Номер папки от").grid(row=1, column=0, sticky="w")
    minimum = ttk.Entry(settings, width=8)
    minimum.insert(0, "110")
    minimum.grid(row=1, column=1, padx=8, pady=4, sticky="w")
    status = tk.StringVar(value="Выберите метку диска. Можно ввести её до подключения.")
    ttk.Label(panel, textvariable=status, wraplength=450).pack(anchor="w", pady=8)
    dates = tk.Listbox(panel, selectmode="extended", exportselection=False)
    dates.pack(fill="both", expand=True)
    buttons = ttk.Frame(panel)
    buttons.pack(fill="x", pady=(10, 0))
    ttk.Button(buttons, text="Все даты", command=lambda: dates.select_set(0, "end")).pack(side="left")
    copy_button = ttk.Button(buttons, text="Копировать на рабочий стол")
    copy_button.pack(side="right")
    pool = ThreadPoolExecutor(max_workers=1)

    current = None
    groups = {}
    days = []
    copying = False

    def refresh(force=False):
        nonlocal current, groups, days
        if copying:
            return
        attached = list(drives())
        options = []
        matches = []
        name = label.get().strip().casefold()
        for path, volume, serial in attached:
            option = f"{path.drive} — {volume}" if volume else path.drive
            options.append(option)
            if name and name in {path.drive.casefold(), option.casefold(), volume.casefold()}:
                matches.append((path, serial))
        label.configure(values=options)
        key = (name, minimum.get(), tuple(matches))
        if key == current and not force:
            return
        current = key
        dates.delete(0, "end")
        groups, days = {}, []
        if len(matches) != 1:
            status.set("Ожидание диска с указанной буквой или меткой." if not matches
                       else "Подключено несколько дисков с этой меткой.")
            return
        try:
            threshold = int(minimum.get())
            if threshold < 0:
                raise ValueError
        except ValueError:
            status.set("Введите целый неотрицательный номер папки.")
            return
        try:
            folder, groups = scan(matches[0][0], threshold)
        except FileNotFoundError:
            status.set("На выбранном диске не найдена папка DCIM.")
            return
        except (OSError, ValueError) as error:
            status.set(str(error))
            return
        days = sorted(groups, reverse=True)
        for day in days:
            dates.insert("end", f"{display_date(day)}   ·   файлов: {len(groups[day])}")
        status.set(f"{folder} · выберите даты" if days else f"{folder} · файлов нет")

    def tick():
        if not copying:
            refresh()
        window.after(1500, tick)

    def start_copy():
        nonlocal copying
        refresh()
        selection = [(days[index], path) for index in dates.curselection()
                     for path in groups[days[index]]]
        if not selection:
            messagebox.showinfo("Импорт", "Выберите одну или несколько дат.")
            return
        copying = True
        copy_button.state(["disabled"])
        status.set(f"Копирование файлов: {len(selection)}")
        future = pool.submit(lambda: copy_files(desktop(), selection))

        def finish():
            nonlocal copying
            if not future.done():
                window.after(150, finish)
                return
            copying = False
            copy_button.state(["!disabled"])
            try:
                copied, skipped, errors = future.result()
                status.set(f"Скопировано: {copied}. Уже были: {skipped}. Ошибок: {len(errors)}.")
                if errors:
                    messagebox.showwarning("Ошибки копирования", "\n".join(errors[:8]))
            except OSError as error:
                status.set(str(error))

        finish()

    def close():
        if copying:
            messagebox.showinfo("Импорт", "Дождитесь окончания копирования.")
            return
        pool.shutdown(wait=False)
        window.destroy()

    ttk.Button(settings, text="Обновить", command=lambda: refresh(True)).grid(
        row=0, column=2, padx=4
    )
    copy_button.configure(command=start_copy)
    window.protocol("WM_DELETE_WINDOW", close)
    tick()
    window.mainloop()


if __name__ == "__main__":
    main()
