import os
import secrets
import sys
import tkinter as tk
from tkinter import messagebox


CHUNK_SIZE_DEFAULT = 8 * 1024 * 1024


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


def overwrite_file(path: str, passes: int = 3, pattern: str = "random+zeros", rename_before_delete: bool = True) -> None:
    st = os.stat(path)
    size = st.st_size
    with open(path, "r+b", buffering=0) as f:
        for p in range(passes):
            pat = pattern_for_pass(passes, p, pattern)
            f.seek(0)
            remaining = size
            while remaining > 0:
                n = min(CHUNK_SIZE_DEFAULT, remaining)
                if pat == "random":
                    buf = random_bytes(n)
                elif pat == "zeros":
                    buf = b"\x00" * n
                elif pat == "ones":
                    buf = b"\xFF" * n
                else:
                    raise ValueError("Неизвестный шаблон перезаписи")
                f.write(buf)
                remaining -= n
            f.flush()
            os.fsync(f.fileno())
    final_path = path
    if rename_before_delete:
        try:
            renamed = rename_path(path)
            os.replace(path, renamed)
            final_path = renamed
        except Exception:
            final_path = path
    try:
        with open(final_path, "r+b", buffering=0) as f:
            f.truncate(0)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass
    os.remove(final_path)


def main() -> int:
    if len(sys.argv) < 2:
        return 2
    path = sys.argv[1]
    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()
    if not os.path.isfile(path):
        messagebox.showerror("Ошибка", "Это не файл или файл не найден.")
        return 2
    st = os.stat(path)
    warning = (
        "Будет выполнена ПЕРЕЗАПИСЬ и УДАЛЕНИЕ файла.\n\n"
        "Важно: на SSD/флеш необратимость не гарантируется, а бэкапы/снимки/синхронизация могут сохранять копии.\n\n"
        f"Файл:\n{path}\n\n"
        f"Размер: {st.st_size} байт\n"
        "Проходов: 3\n"
        "Шаблон: random+zeros\n\n"
        "Продолжить?"
    )
    if not messagebox.askyesno("Подтверждение", warning, icon="warning"):
        return 0
    try:
        overwrite_file(path)
    except Exception as e:
        messagebox.showerror("Ошибка", f"Не удалось удалить файл:\n\n{type(e).__name__}: {e}")
        return 1
    messagebox.showinfo("Готово", "Файл перезаписан и удалён.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

