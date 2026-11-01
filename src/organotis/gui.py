"""Γραφικό περιβάλλον (tkinter): προεπισκόπηση με αφαίρεση γραμμών και μετακίνηση.

Η λογική βρίσκεται στο PreviewModel και στον μεταφορέα· εδώ είναι μόνο η οθόνη.
Η σάρωση και η μετακίνηση τρέχουν σε νήμα, ώστε το παράθυρο να μην παγώνει.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, config as cfgmod
from .approval import PreviewModel
from .mover import execute
from .planner import TASKS, build_plan, normalize
from .scanner import build_report, scan
from .undo import list_runs, undo as run_undo


class App:
    def __init__(self, root: tk.Tk, config_path: Path | None = None):
        self.root = root
        self.config_path = config_path or cfgmod.default_config_path()
        self.cfg = cfgmod.load(self.config_path)
        self.model: PreviewModel | None = None
        self.q: queue.Queue = queue.Queue()
        self.stop = threading.Event()
        self.busy = False

        root.title(f"Οργανωτής αρχείων {__version__} (δοκιμαστική έκδοση)")
        root.geometry("1100x620")

        top = ttk.Frame(root, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Φάκελος:").pack(side="left")
        self.folder = tk.StringVar(value=self.cfg.scan_roots[0] if self.cfg.scan_roots else "")
        ttk.Entry(top, textvariable=self.folder, width=60).pack(side="left", padx=6, fill="x", expand=True)
        ttk.Button(top, text="Επιλογή…", command=self.choose).pack(side="left")
        self.btn_scan = ttk.Button(top, text="Σάρωση και πρόταση", command=self.start_scan)
        self.btn_scan.pack(side="left", padx=6)

        opts = ttk.LabelFrame(root, text="Εργασίες (με τη σειρά που εκτελούνται)", padding=6)
        opts.pack(fill="x", padx=8)
        self.task_vars = {t: tk.BooleanVar(value=True) for t in TASKS}
        for t, label in (("duplicates", "1. Διπλότυπα"), ("same_names", "2. Ίδια ονόματα"),
                         ("general", "3. Γενική ταξινόμηση")):
            ttk.Checkbutton(opts, text=label, variable=self.task_vars[t],
                            command=self.tasks_changed).pack(side="left", padx=(0, 12))
        prot = ttk.Frame(opts)
        prot.pack(side="right")
        ttk.Label(prot, text="Προστατευμένοι φάκελοι (δεν σπάνε):").pack(side="left")
        self.prot_list = tk.Listbox(prot, height=2, width=28, exportselection=False)
        self.prot_list.pack(side="left", padx=6)
        ttk.Button(prot, text="Προσθήκη προστατευμένου φακέλου…",
                   command=self.add_protected).pack(side="left")
        ttk.Button(prot, text="Αφαίρεση", command=self.remove_protected).pack(side="left", padx=4)
        self.refresh_protected()

        cols = ("n", "action", "src", "dest", "reason")
        frame = ttk.Frame(root, padding=(8, 0))
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="extended")
        for c, title, w in (("n", "Αρ.", 45), ("action", "Ενέργεια", 190), ("src", "Από", 260),
                            ("dest", "Προς", 300), ("reason", "Λόγος", 700)):
            self.tree.heading(c, text=title)
            self.tree.column(c, width=w, anchor="w", stretch=(c == "reason"))
        ys = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        xs = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.tree.tag_configure("removed", foreground="#888888")
        self.tree.bind("<<TreeviewSelect>>", self.show_detail)
        self.tree.bind("<Delete>", lambda e: self.remove_selected())

        self.detail = tk.Text(root, height=4, wrap="word", state="disabled")
        self.detail.pack(fill="x", padx=8, pady=(6, 0))

        bar = ttk.Frame(root, padding=8)
        bar.pack(fill="x")
        self.btn_remove = ttk.Button(bar, text="Αφαίρεση επιλεγμένων (Delete)", command=self.remove_selected)
        self.btn_remove.pack(side="left")
        self.btn_restore = ttk.Button(bar, text="Επαναφορά επιλεγμένων", command=self.restore_selected)
        self.btn_restore.pack(side="left", padx=6)
        self.btn_undo = ttk.Button(bar, text="Αναίρεση…", command=self.open_undo)
        self.btn_undo.pack(side="left", padx=(24, 0))
        self.btn_cancel = ttk.Button(bar, text="Διακοπή", command=self.stop.set, state="disabled")
        self.btn_cancel.pack(side="right")
        self.btn_go = ttk.Button(bar, text="Μετακίνηση εγκεκριμένων…", command=self.confirm_and_move, state="disabled")
        self.btn_go.pack(side="right", padx=6)
        self.status = tk.StringVar(value="Διάλεξε φάκελο και πάτα «Σάρωση και πρόταση». Δεν μετακινείται τίποτα πριν την έγκρισή σου.")
        ttk.Label(root, textvariable=self.status, padding=(8, 0, 8, 8)).pack(fill="x")
        root.after(100, self.poll)

    # ---- ενέργειες ----
    def choose(self) -> None:
        d = filedialog.askdirectory(title="Φάκελος προς σάρωση")
        if d:
            self.folder.set(d)

    # ---- εργασίες και προστατευμένοι φάκελοι ----
    def selected_tasks(self) -> tuple[str, ...]:
        return tuple(t for t in TASKS if self.task_vars[t].get())

    def tasks_changed(self) -> None:
        # Η προηγούμενη πρόταση δεν ισχύει πια: ζητάμε νέα σάρωση.
        if self.model is not None:
            self.model = None
            self.render()
        self.status.set("Άλλαξαν οι εργασίες. Πάτα «Σάρωση και πρόταση» για νέα πρόταση.")

    def refresh_protected(self) -> None:
        self.prot_list.delete(0, "end")
        for n in self.cfg.protected_folders:
            self.prot_list.insert("end", n)

    def _save_protected(self, names: list[str]) -> bool:
        old = self.cfg.protected_folders
        self.cfg.protected_folders = names
        try:
            cfgmod.save(self.cfg, self.config_path)
        except (cfgmod.ConfigError, OSError) as exc:
            self.cfg.protected_folders = old
            messagebox.showerror("Σφάλμα ρυθμίσεων", str(exc))
            return False
        self.refresh_protected()
        self.tasks_changed()
        return True

    def add_protected_name(self, name: str) -> bool:
        name = name.strip()
        if any(normalize(n) == normalize(name) for n in self.cfg.protected_folders):
            return False
        return self._save_protected(self.cfg.protected_folders + [name])

    def add_protected(self) -> None:
        d = filedialog.askdirectory(title="Φάκελος που δεν πρέπει να σπάσει")
        if d:
            self.add_protected_name(Path(d).name)

    def remove_protected(self) -> None:
        sel = self.prot_list.curselection()
        if sel:
            names = list(self.cfg.protected_folders)
            del names[sel[0]]
            self._save_protected(names)

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.btn_scan.configure(state="disabled" if busy else "normal")
        self.btn_undo.configure(state="disabled" if busy else "normal")
        self.btn_cancel.configure(state="normal" if busy else "disabled")
        self.refresh_buttons()

    def start_scan(self) -> None:
        folder = self.folder.get().strip()
        if not folder or self.busy:
            return
        tasks = self.selected_tasks()
        if not tasks:
            messagebox.showwarning("Εργασίες", "Διάλεξε τουλάχιστον μία εργασία.")
            return
        self.set_busy(True)
        self.status.set("Σάρωση… (μόνο ανάγνωση)")

        def work() -> None:
            try:
                res = scan(Path(folder).expanduser(), self.cfg)
                self.q.put(("plan", build_plan(res, build_report(res), self.cfg, tasks=tasks)))
            except Exception as exc:  # εμφανίζεται στον χρήστη, δεν κρύβεται
                self.q.put(("error", f"Η σάρωση απέτυχε: {exc}"))
        threading.Thread(target=work, daemon=True).start()

    def load_plan(self, plan) -> None:
        self.model = PreviewModel(plan)
        self.render()
        n = len(self.model.rows)
        self.status.set(f"{n} προτάσεις. Αφαίρεσε όσες δεν θέλεις και πάτα «Μετακίνηση εγκεκριμένων…»."
                        if n else "Δεν υπάρχει τίποτα να μετακινηθεί: όλα μένουν στη θέση τους.")

    def render(self) -> None:
        self.tree.delete(*self.tree.get_children())
        if not self.model:
            return
        root = self.model.root
        for r in self.model.rows:
            p = r.proposal
            action = "Φάκελος ελέγχου" if p.action == "review" else "Μετακίνηση"
            tags = ("removed",) if r.number in self.model.removed else ()
            def rel(x: Path) -> str:
                try:
                    return str(x.relative_to(root))
                except ValueError:
                    return str(x)
            self.tree.insert("", "end", iid=str(r.number), tags=tags, values=(
                r.number, action + (" (αφαιρέθηκε)" if tags else ""),
                rel(p.entry.path), rel(p.dest), f"[{p.rule}] {p.reason}"))
        self.refresh_buttons()

    def selected_numbers(self) -> list[int]:
        return [int(i) for i in self.tree.selection()]

    def remove_selected(self) -> None:
        if self.model and not self.busy:
            self.model.remove(self.selected_numbers())
            self.render()

    def restore_selected(self) -> None:
        if self.model and not self.busy:
            self.model.restore(self.selected_numbers())
            self.render()

    def refresh_buttons(self) -> None:
        can = bool(self.model and self.model.active) and not self.busy
        self.btn_go.configure(state="normal" if can else "disabled")
        if self.model:
            self.btn_go.configure(text=f"Μετακίνηση εγκεκριμένων ({len(self.model.active)})…")

    def show_detail(self, _evt=None) -> None:
        sel = self.selected_numbers()
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        if self.model and sel:
            p = next(r.proposal for r in self.model.rows if r.number == sel[0])
            self.detail.insert("1.0", f"Λόγος [{p.rule}]\n{p.reason}")
        self.detail.configure(state="disabled")

    def confirm_and_move(self) -> None:
        if not self.model or self.busy:
            return
        active = self.model.active
        reviews = sum(1 for r in active if r.proposal.action == "review")
        if not messagebox.askyesno(
                "Έγκριση μετακίνησης",
                f"Θα μετακινηθούν {len(active)} αρχεία ({reviews} στον φάκελο ελέγχου).\n"
                "Δεν διαγράφεται τίποτα και θα γραφτεί manifest για αναίρεση.\n\nΣυνέχεια;"):
            return
        approved = self.model.approve()
        self.stop.clear()
        self.set_busy(True)
        mdir = self.cfg.manifest_path(self.config_path)

        def work() -> None:
            try:
                res = execute(approved, mdir, should_stop=self.stop.is_set,
                              progress=lambda i, n, p: self.q.put(("progress", (i, n))))
                self.q.put(("done", res))
            except Exception as exc:
                self.q.put(("error", f"Η μετακίνηση σταμάτησε: {exc}"))
        threading.Thread(target=work, daemon=True).start()

    # ---- αναίρεση ----
    def open_undo(self) -> None:
        if self.busy:
            return
        self.undo_runs = list_runs(self.cfg.manifest_path(self.config_path))
        win = self.undo_win = tk.Toplevel(self.root)
        win.title("Αναίρεση εκτέλεσης")
        ttk.Label(win, text="Διάλεξε εκτέλεση. Τα αρχεία επιστρέφουν στην αρχική τους θέση "
                            "(όχι πάνω σε άλλα αρχεία, χωρίς διαγραφές).", padding=8).pack(anchor="w")
        self.undo_list = tk.Listbox(win, width=100, height=12, exportselection=False)
        for r in self.undo_runs:
            self.undo_list.insert("end", f"{r.started.replace('T', ' ')}   {r.moved} αρχεία   "
                                         f"{r.label}   [{r.root}]")
        self.undo_list.pack(fill="both", expand=True, padx=8)
        if not self.undo_runs:
            ttk.Label(win, text="Δεν υπάρχουν καταγεγραμμένες εκτελέσεις.", padding=8).pack()
        ttk.Button(win, text="Αναίρεση επιλεγμένης…", command=self.confirm_and_undo).pack(pady=8)

    def confirm_and_undo(self) -> None:
        sel = self.undo_list.curselection()
        if not sel or self.busy:
            return
        run = self.undo_runs[sel[0]]
        if run.remaining == 0:
            messagebox.showinfo("Αναίρεση", "Σε αυτή την εκτέλεση δεν υπάρχει τίποτα προς αναίρεση.")
            return
        if not messagebox.askyesno(
                "Έγκριση αναίρεσης",
                f"Θα επιστραφούν έως {run.remaining} αρχεία στην αρχική τους θέση.\n"
                "Όσα άλλαξαν ή έχουν πιαστεί οι θέσεις τους θα παραλειφθούν και θα σου εμφανιστούν.\n\n"
                "Συνέχεια;"):
            return
        self.undo_win.destroy()
        self.stop.clear()
        self.set_busy(True)

        def work() -> None:
            try:
                res = run_undo(run.path, should_stop=self.stop.is_set,
                               progress=lambda i, n, op: self.q.put(("uprogress", (i, n))))
                self.q.put(("undone", res))
            except Exception as exc:
                self.q.put(("error", f"Η αναίρεση σταμάτησε: {exc}"))
        threading.Thread(target=work, daemon=True).start()

    def finish_undo(self, res) -> None:
        msg = f"Επέστρεψαν {res.restored}, παραλείφθηκαν {len(res.skipped)}, αφαιρέθηκαν {res.removed_dirs} άδειοι φάκελοι."
        if res.status == "stopped":
            msg += " (Διακόπηκε από εσένα.)"
        if res.skipped:
            msg += "\n\nΠαραλείφθηκαν:\n" + "\n".join(
                f"• {op.src[-1]}: {why}" for op, why in res.skipped[:15])
            msg += "\n\nΜπορείς να ξαναδοκιμάσεις αργότερα."
        self.status.set(msg.split("\n")[0])
        messagebox.showinfo("Αναίρεση", msg)

    # ---- ουρά μηνυμάτων από τα νήματα ----
    def poll(self) -> None:
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "plan":
                    self.set_busy(False)
                    self.load_plan(data)
                elif kind == "progress":
                    self.status.set(f"Μετακίνηση {data[0]} από {data[1]}…")
                elif kind == "uprogress":
                    self.status.set(f"Αναίρεση {data[0]} από {data[1]}…")
                elif kind == "undone":
                    self.set_busy(False)
                    self.finish_undo(data)
                elif kind == "done":
                    self.set_busy(False)
                    self.finish(data)
                elif kind == "error":
                    self.set_busy(False)
                    self.status.set(data)
                    messagebox.showerror("Σφάλμα", data)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def finish(self, res) -> None:
        msg = f"Μετακινήθηκαν {res.moved}, παραλείφθηκαν {len(res.skipped)}."
        if res.status == "stopped":
            msg += " (Διακόπηκε από εσένα.)"
        if res.skipped:
            msg += "\n\nΠαραλείφθηκαν:\n" + "\n".join(
                f"• {p.entry.path.name}: {why}" for p, why in res.skipped[:15])
        self.status.set(msg.split("\n")[0] + f"  Manifest: {res.manifest_path}")
        self.model = None
        self.render()
        messagebox.showinfo("Τέλος", msg + f"\n\nManifest (για αναίρεση):\n{res.manifest_path}")


def run() -> int:
    root = tk.Tk()
    try:
        App(root)
    except cfgmod.ConfigError as exc:
        messagebox.showerror("Σφάλμα ρυθμίσεων", str(exc))
        return 2
    root.mainloop()
    return 0
