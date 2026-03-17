import os
import secrets
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


CHUNK_SIZE_DEFAULT = 8 * 1024 * 1024


class Cancelled(Exception):
    pass


def human_bytes(n: int) -> str:
    units = ["Б", "КиБ", "МиБ", "ГиБ", "ТиБ"]
    v = float(max(0, n))
    for u in units:
        if v < 1024.0 or u == units[-1]:
            if u == "Б":
                return f"{int(v)} {u}"
            return f"{v:.2f} {u}"
        v /= 1024.0
    return f"{n} Б"


def random_bytes(n: int) -> bytes:
    return secrets.token_bytes(n)


def rename_path(path: str) -> str:
    folder = os.path.dirname(path)
    candidate = os.path.join(folder, secrets.token_hex(16))
    for _ in range(32):
        if not os.path.exists(candidate):
            return candidate
        candidate = os.path.join(folder, secrets.token_hex(16))
    return candidate


def pattern_for_pass(passes: int, pass_index: int, pattern: str) -> str:
    if pattern != "random+zeros":
        return pattern
    return "zeros" if pass_index == passes - 1 else "random"


def check_cancel(state: dict) -> None:
    if state["cancel_event"].is_set():
        raise Cancelled()


def emit(state: dict, kind: str, payload) -> None:
    with state["queue_lock"]:
        state["queue"].append((kind, payload))


def log_line(state: dict, msg: str) -> None:
    emit(state, "log", msg)


def overwrite_stream(state: dict, f, size: int, pattern: str, chunk_size: int) -> None:
    f.seek(0)
    remaining = size
    zeros_small = b"\x00" * min(chunk_size, 1024 * 1024)
    ones_small = b"\xFF" * min(chunk_size, 1024 * 1024)
    while remaining > 0:
        check_cancel(state)
        n = min(chunk_size, remaining)
        if pattern == "random":
            buf = random_bytes(n)
        elif pattern == "zeros":
            buf = zeros_small[:n] if n <= len(zeros_small) else (b"\x00" * n)
        elif pattern == "ones":
            buf = ones_small[:n] if n <= len(ones_small) else (b"\xFF" * n)
        else:
            raise ValueError("Неизвестный шаблон перезаписи")
        f.write(buf)
        remaining -= n
        emit(state, "progress_add", n)
    f.flush()
    os.fsync(f.fileno())


def verify_stream(state: dict, f, size: int, pattern: str, chunk_size: int) -> None:
    if pattern == "random":
        log_line(state, " Проверка пропущена для случайных данных.")
        return
    f.seek(0)
    remaining = size
    expected_byte = b"\x00" if pattern == "zeros" else b"\xFF"
    while remaining > 0:
        check_cancel(state)
        n = min(chunk_size, remaining)
        data = f.read(n)
        if len(data) != n:
            raise OSError("Ошибка чтения при проверке.")
        if data != expected_byte * n:
            raise OSError("Проверка не пройдена: данные не совпадают.")
        remaining -= n
    log_line(state, " Проверка OK.")


def wipe_one_file(state: dict, path: str, plan: dict) -> str:
    check_cancel(state)
    st = os.stat(path)
    size = st.st_size
    with open(path, "r+b", buffering=0) as f:
        for p in range(plan["passes"]):
            check_cancel(state)
            pat = pattern_for_pass(plan["passes"], p, plan["pattern"])
            log_line(state, f" Проход {p + 1}/{plan['passes']}: {pat}")
            overwrite_stream(state, f, size=size, pattern=pat, chunk_size=plan["chunk_size"])
            if plan["verify_last_pass"] and p == plan["passes"] - 1:
                verify_stream(state, f, size=size, pattern=pat, chunk_size=plan["chunk_size"])
    final_path = path
    if plan["rename_before_delete"]:
        try:
            renamed = rename_path(path)
            os.replace(path, renamed)
            final_path = renamed
            log_line(state, f" Переименовано -> {final_path}")
        except Exception as e:
            log_line(state, f" Переименование не удалось (продолжаю): {type(e).__name__}: {e}")
            final_path = path
    try:
        with open(final_path, "r+b", buffering=0) as f:
            f.truncate(0)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass
    os.remove(final_path)
    log_line(state, " Удалено.")
    return path


def worker_run(state: dict, files: list[str], plan: dict) -> None:
    failures: list[str] = []
    wiped_paths: list[str] = []
    wiped = 0
    try:
        for idx, path in enumerate(files, start=1):
            check_cancel(state)
            emit(state, "status", f"Перезапись {idx}/{len(files)}: {path}")
            log_line(state, f"Файл: {path}")
            try:
                wiped_paths.append(wipe_one_file(state, path, plan))
                wiped += 1
            except Cancelled:
                raise
            except Exception as e:
                failures.append(f"{path}\n  {type(e).__name__}: {e}")
                log_line(state, f"ОШИБКА: {path} ({type(e).__name__}: {e})")
    except Cancelled:
        msg = f"Отменено. Успешно обработано: {wiped} файл(ов)."
        if failures:
            msg += "\n\nОшибки:\n" + "\n".join(failures[:20])
            if len(failures) > 20:
                msg += f"\n… и ещё {len(failures) - 20}."
        emit(state, "done", (False, msg, wiped_paths))
        return
    except Exception as e:
        emit(state, "done", (False, f"Неожиданная ошибка: {type(e).__name__}: {e}", wiped_paths))
        return
    if failures:
        msg = f"Завершено с ошибками. Успешно обработано: {wiped} файл(ов).\n\nОшибки:\n" + "\n".join(failures[:20])
        if len(failures) > 20:
            msg += f"\n… и ещё {len(failures) - 20}."
        emit(state, "done", (False, msg, wiped_paths))
    else:
        emit(state, "done", (True, f"Готово. Успешно удалено: {wiped} файл(ов).", wiped_paths))


def pump_queue(state: dict) -> None:
    items = []
    with state["queue_lock"]:
        if state["queue"]:
            items = state["queue"][:]
            state["queue"].clear()
    for kind, payload in items:
        if kind == "log":
            ts = time.strftime("%H:%M:%S")
            state["log"].insert("end", f"[{ts}] {payload}\n")
            state["log"].see("end")
        elif kind == "status":
            state["status_var"].set(str(payload))
        elif kind == "progress_add":
            try:
                state["progress"].configure(value=float(state["progress"]["value"]) + float(payload))
            except Exception:
                pass
        elif kind == "done":
            ok, msg, wiped_paths = payload
            state["start_btn"].configure(state="normal")
            state["cancel_btn"].configure(state="disabled")
            state["status_var"].set("Готово." if ok else "Остановлено.")
            if wiped_paths:
                lb = state["files_list"]
                current = [lb.get(i) for i in range(lb.size())]
                keep = [p for p in current if p not in set(wiped_paths)]
                lb.delete(0, "end")
                for p in keep:
                    lb.insert("end", p)
            if ok:
                messagebox.showinfo("Завершено", msg)
            else:
                messagebox.showwarning("Остановлено", msg)
    state["root"].after(80, lambda: pump_queue(state))


def all_files(state: dict) -> list[str]:
    lb = state["files_list"]
    return [lb.get(i) for i in range(lb.size())]


def selected_files(state: dict) -> list[str]:
    lb = state["files_list"]
    sel = list(lb.curselection())
    return [lb.get(i) for i in sel] if sel else []


def ui_add_files(state: dict) -> None:
    paths = filedialog.askopenfilenames(title="Выберите файлы для безопасного удаления")
    if not paths:
        return
    existing = set(all_files(state))
    for p in paths:
        if p and p not in existing:
            state["files_list"].insert("end", p)


def ui_add_folder(state: dict) -> None:
    folder = filedialog.askdirectory(title="Выберите папку (будут добавлены все файлы внутри)")
    if not folder:
        return
    existing = set(all_files(state))
    added = 0
    for root_dir, _, files in os.walk(folder):
        for name in files:
            p = os.path.join(root_dir, name)
            if p not in existing:
                state["files_list"].insert("end", p)
                existing.add(p)
                added += 1
    log_line(state, f"Добавлено файлов из папки: {added}.")


def ui_remove_selected(state: dict) -> None:
    lb = state["files_list"]
    sel = list(lb.curselection())
    sel.reverse()
    for idx in sel:
        lb.delete(idx)


def ui_clear(state: dict) -> None:
    state["files_list"].delete(0, "end")


def ui_cancel(state: dict) -> None:
    if state["worker_thread"] and state["worker_thread"].is_alive():
        state["cancel_event"].set()
        state["status_var"].set("Отмена… (дождитесь завершения текущего блока)")
        log_line(state, "Запрошена отмена.")


def ui_start(state: dict) -> None:
    if state["worker_thread"] and state["worker_thread"].is_alive():
        messagebox.showinfo("Занято", "Удаление уже выполняется.")
        return
    files = selected_files(state)
    if not files:
        messagebox.showwarning("Нет выбора", "Сначала выделите один или несколько файлов в списке.")
        return
    try:
        passes = int(state["passes_var"].get().strip())
        if passes < 1 or passes > 35:
            raise ValueError
    except Exception:
        messagebox.showerror("Неверно", "Число проходов должно быть целым от 1 до 35.")
        return
    pattern = state["pattern_var"].get().strip()
    if pattern not in {"random", "zeros", "ones", "random+zeros"}:
        messagebox.showerror("Неверно", "Выберите корректный шаблон перезаписи.")
        return
    try:
        chunk_size = int(state["chunk_var"].get().strip())
        if chunk_size < 4096 or chunk_size > 256 * 1024 * 1024:
            raise ValueError
    except Exception:
        messagebox.showerror("Неверно", "Размер блока должен быть целым числом от 4096 до 268435456 байт.")
        return
    plan = {
        "passes": passes,
        "pattern": pattern,
        "rename_before_delete": bool(state["rename_var"].get()),
        "verify_last_pass": bool(state["verify_var"].get()),
        "chunk_size": chunk_size,
    }
    total = 0
    missing: list[str] = []
    for p in files:
        try:
            st = os.stat(p)
            total += st.st_size
        except FileNotFoundError:
            missing.append(p)
    if missing:
        messagebox.showwarning("Файлы не найдены", "Некоторые файлы больше не существуют:\n\n" + "\n".join(missing))
        files = [p for p in files if p not in missing]
        if not files:
            return
    warning = (
        "Будут ВЫПОЛНЕНЫ ПЕРЕЗАПИСЬ и УДАЛЕНИЕ выбранных файлов.\n\n"
        "Важные ограничения:\n"
        "- SSD/флеш: из-за wear leveling перезапись может не затронуть старые данные.\n"
        "- Журналирование/снимки/копии ОС, облачная синхронизация, бэкапы, антивирус и временные файлы могут сохранить копии.\n"
        "- Если файл занят/заблокирован, операция может не выполниться.\n\n"
        f"Выбрано: {len(files)} файл(ов)\n"
        f"Общий размер: {human_bytes(total)}\n"
        f"Проходов: {plan['passes']}\n"
        f"Шаблон: {plan['pattern']}\n\n"
        "Продолжить?"
    )
    if not messagebox.askyesno("Подтверждение", warning, icon="warning"):
        return
    state["cancel_event"].clear()
    state["start_btn"].configure(state="disabled")
    state["cancel_btn"].configure(state="normal")
    state["progress"].configure(value=0, maximum=max(1, total * plan["passes"]))
    state["status_var"].set("Запуск…")
    log_line(state, f"Старт. Файлов: {len(files)}.")
    t = threading.Thread(target=worker_run, args=(state, files, plan), daemon=True)
    state["worker_thread"] = t
    t.start()


def build_ui(state: dict) -> None:
    root = state["root"]
    root.title("Безопасное удаление (перезапись + удаление)")
    root.geometry("1120x740")
    root.resizable(False, False)
    main = ttk.Frame(root, padding=12)
    main.pack(fill="both", expand=True)
    ttk.Label(
        main,
        text=(
            "Внимание: гарантировать необратимость нельзя на SSD/флеш-носителях, "
            "некоторых файловых системах, а также при наличии бэкапов/снимков/синхронизации."
        ),
        foreground="#9a1b1b",
        wraplength=1080,
    ).pack(anchor="w")
    files_frame = ttk.LabelFrame(main, text="Файлы", padding=10)
    files_frame.pack(fill="both", expand=True, pady=(10, 10))
    lb = tk.Listbox(files_frame, selectmode="extended", height=12)
    lb.pack(side="left", fill="both", expand=True)
    sb = ttk.Scrollbar(files_frame, orient="vertical", command=lb.yview)
    sb.pack(side="left", fill="y")
    lb.configure(yscrollcommand=sb.set)
    state["files_list"] = lb
    btns = ttk.Frame(files_frame)
    btns.pack(side="left", fill="y", padx=(10, 0))
    ttk.Button(btns, text="Добавить файлы…", command=lambda: ui_add_files(state)).pack(fill="x")
    ttk.Button(btns, text="Добавить папку…", command=lambda: ui_add_folder(state)).pack(fill="x", pady=(8, 0))
    ttk.Button(btns, text="Убрать выделенные", command=lambda: ui_remove_selected(state)).pack(fill="x", pady=(8, 0))
    ttk.Button(btns, text="Очистить список", command=lambda: ui_clear(state)).pack(fill="x", pady=(8, 0))
    opts = ttk.LabelFrame(main, text="Параметры", padding=10)
    opts.pack(fill="x")
    grid = ttk.Frame(opts)
    grid.pack(fill="x")
    grid.columnconfigure(1, weight=1)
    grid.columnconfigure(3, weight=1)
    ttk.Label(grid, text="Проходов:").grid(row=0, column=0, sticky="w")
    state["passes_var"] = tk.StringVar(value="3")
    ttk.Spinbox(grid, from_=1, to=35, textvariable=state["passes_var"], width=6).grid(
        row=0, column=1, sticky="w", padx=(8, 18)
    )
    ttk.Label(grid, text="Шаблон:").grid(row=0, column=2, sticky="w")
    state["pattern_var"] = tk.StringVar(value="random+zeros")
    ttk.Combobox(
        grid,
        textvariable=state["pattern_var"],
        values=["random", "zeros", "ones", "random+zeros"],
        state="readonly",
        width=18,
    ).grid(row=0, column=3, sticky="w", padx=(8, 0))
    state["rename_var"] = tk.BooleanVar(value=True)
    ttk.Checkbutton(grid, variable=state["rename_var"], text="Переименовать перед удалением").grid(
        row=1, column=0, columnspan=2, sticky="w", pady=(10, 0)
    )
    state["verify_var"] = tk.BooleanVar(value=False)
    ttk.Checkbutton(grid, variable=state["verify_var"], text="Проверить последний проход (медленнее)").grid(
        row=1, column=2, columnspan=2, sticky="w", pady=(10, 0)
    )
    ttk.Label(grid, text="Размер блока:").grid(row=2, column=0, sticky="w", pady=(10, 0))
    state["chunk_var"] = tk.StringVar(value=str(CHUNK_SIZE_DEFAULT))
    ttk.Entry(grid, textvariable=state["chunk_var"], width=14).grid(
        row=2, column=1, sticky="w", padx=(8, 18), pady=(10, 0)
    )
    ttk.Label(grid, text="байт").grid(row=2, column=2, columnspan=2, sticky="w", pady=(10, 0))
    actions = ttk.Frame(main)
    actions.pack(fill="x", pady=(10, 0))
    start_btn = ttk.Button(actions, text="Безопасно удалить выделенные файлы", command=lambda: ui_start(state))
    start_btn.pack(side="left")
    cancel_btn = ttk.Button(actions, text="Отмена", command=lambda: ui_cancel(state), state="disabled")
    cancel_btn.pack(side="left", padx=(10, 0))
    state["start_btn"] = start_btn
    state["cancel_btn"] = cancel_btn
    prog = ttk.Progressbar(main, orient="horizontal", mode="determinate")
    prog.pack(fill="x", pady=(10, 0))
    state["progress"] = prog
    state["status_var"] = tk.StringVar(value="Ожидание.")
    ttk.Label(main, textvariable=state["status_var"]).pack(anchor="w", pady=(6, 0))
    log_frame = ttk.LabelFrame(main, text="Журнал", padding=10)
    log_frame.pack(fill="both", expand=True, pady=(10, 0))
    txt = tk.Text(log_frame, height=16, wrap="word")
    txt.pack(side="left", fill="both", expand=True)
    txt_sb = ttk.Scrollbar(log_frame, orient="vertical", command=txt.yview)
    txt_sb.pack(side="left", fill="y")
    txt.configure(yscrollcommand=txt_sb.set)
    state["log"] = txt


def main() -> None:
    state = {
        "root": tk.Tk(),
        "worker_thread": None,
        "cancel_event": threading.Event(),
        "queue": [],
        "queue_lock": threading.Lock(),
    }
    build_ui(state)
    pump_queue(state)
    state["root"].mainloop()


if __name__ == "__main__":
    main()

