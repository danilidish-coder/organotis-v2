"""Γραφικό περιβάλλον (tkinter): προεπισκόπηση, έγκριση, μάθηση από διορθώσεις, μετακίνηση, αναίρεση.

Η λογική βρίσκεται στο PreviewModel, στους κανόνες (learning) και στον μεταφορέα· εδώ είναι μόνο η οθόνη.
Η σάρωση και η μετακίνηση τρέχουν σε νήμα, ώστε το παράθυρο να μην παγώνει.
Όλα γίνονται τοπικά: δεν στέλνεται τίποτα πουθενά.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, config as cfgmod, learning
from .approval import PreviewModel
from .formatting import CONFIDENCE_LABELS, confidence_line
from .mover import execute
from .planner import SURE, build_plan
from .scanner import build_report, scan
from .undo import list_runs, undo as run_undo

SKIP_ALL = object()  # «δεν θέλω άλλες ερωτήσεις για κανόνες τώρα»
STATUS_LABELS = {"approved": "Εγκεκριμένη", "pending": "Σε αναμονή", "removed": "Αφαιρέθηκε"}


class App:
    def __init__(self, root: tk.Tk, config_path: Path | None = None):
        self.root = root
        self.config_path = config_path or cfgmod.default_config_path()
        self.cfg = cfgmod.load(self.config_path)
        self.model: PreviewModel | None = None
        self.scan_result = None      # κρατιέται ώστε να ξαναχτίζεται η πρόταση χωρίς νέα σάρωση
        self.report = None
        self.overrides: list[dict] = []  # αλλαγές «μόνο αυτή τη φορά»
        self.note = ""                   # τελευταίο μήνυμα, π.χ. «Νέος κανόνας…»
        self._serial = 0
        self.q: queue.Queue = queue.Queue()
        self.stop = threading.Event()
        self.busy = False

        root.title(f"Οργανωτής αρχείων {__version__} (δοκιμαστική έκδοση)")
        root.geometry("1250x700")

        top = ttk.Frame(root, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Φάκελος:").pack(side="left")
        self.folder = tk.StringVar(value=self.cfg.scan_roots[0] if self.cfg.scan_roots else "")
        ttk.Entry(top, textvariable=self.folder, width=60).pack(side="left", padx=6, fill="x", expand=True)
        ttk.Button(top, text="Επιλογή…", command=self.choose).pack(side="left")
        self.btn_scan = ttk.Button(top, text="Σάρωση και πρόταση", command=self.start_scan)
        self.btn_scan.pack(side="left", padx=6)

        cols = ("n", "status", "conf", "src", "dest", "reason")
        frame = ttk.Frame(root, padding=(8, 0))
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="extended")
        for c, title, w in (("n", "Αρ.", 45), ("status", "Κατάσταση", 100), ("conf", "Βεβαιότητα", 100),
                            ("src", "Από", 230), ("dest", "Προς", 280), ("reason", "Λόγος", 700)):
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
        self.tree.tag_configure("approved", background="#dff3e3", foreground="#000000")
        self.tree.tag_configure("check", foreground="#a15c00")
        self.tree.bind("<<TreeviewSelect>>", self.show_detail)
        self.tree.bind("<Delete>", lambda e: self.remove_selected())

        self.detail = tk.Text(root, height=5, wrap="word", state="disabled")
        self.detail.pack(fill="x", padx=8, pady=(6, 0))

        bar1 = ttk.Frame(root, padding=(8, 8, 8, 0))
        bar1.pack(fill="x")
        self.btn_sure = ttk.Button(bar1, text="Έγκριση όλων των σίγουρων", command=self.approve_all_sure)
        self.btn_sure.pack(side="left")
        ttk.Button(bar1, text="Έγκριση επιλεγμένων", command=self.approve_selected_rows).pack(side="left", padx=6)
        self.btn_remove = ttk.Button(bar1, text="Αφαίρεση επιλεγμένων (Delete)", command=self.remove_selected)
        self.btn_remove.pack(side="left")
        ttk.Button(bar1, text="Επαναφορά επιλεγμένων", command=self.restore_selected).pack(side="left", padx=6)
        ttk.Button(bar1, text="Αλλαγή θέματος…", command=self.change_topic).pack(side="left")

        bar2 = ttk.Frame(root, padding=8)
        bar2.pack(fill="x")
        ttk.Button(bar2, text="Κανόνες που έμαθε…", command=self.open_rules).pack(side="left")
        self.btn_undo = ttk.Button(bar2, text="Αναίρεση…", command=self.open_undo)
        self.btn_undo.pack(side="left", padx=(12, 0))
        self.btn_cancel = ttk.Button(bar2, text="Διακοπή", command=self.stop.set, state="disabled")
        self.btn_cancel.pack(side="right")
        self.btn_go = ttk.Button(bar2, text="Μετακίνηση εγκεκριμένων…", command=self.confirm_and_move, state="disabled")
        self.btn_go.pack(side="right", padx=6)
        self.status = tk.StringVar(value="Διάλεξε φάκελο και πάτα «Σάρωση και πρόταση». Δεν μετακινείται τίποτα πριν την έγκρισή σου.")
        ttk.Label(root, textvariable=self.status, padding=(8, 0, 8, 8)).pack(fill="x")
        root.after(100, self.poll)

    # ---- σάρωση ----
    def choose(self) -> None:
        d = filedialog.askdirectory(title="Φάκελος προς σάρωση")
        if d:
            self.folder.set(d)

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
        self.set_busy(True)
        self.status.set("Σάρωση… (μόνο ανάγνωση)")

        def work() -> None:
            try:
                res = scan(Path(folder).expanduser(), self.cfg)
                rep = build_report(res)
                self.q.put(("plan", (res, rep, build_plan(res, rep, self.cfg))))  # νέα σάρωση = χωρίς παλιές αλλαγές «μίας φοράς»
            except Exception as exc:  # εμφανίζεται στον χρήστη, δεν κρύβεται
                self.q.put(("error", f"Η σάρωση απέτυχε: {exc}"))
        threading.Thread(target=work, daemon=True).start()

    def load_plan(self, plan, result=None, report=None) -> None:
        self.scan_result, self.report = result, report
        self.overrides = []
        self.note = ""
        self.model = PreviewModel(plan)
        self.render()

    def replan(self) -> None:
        """Ξαναχτίζει την πρόταση (π.χ. μετά από νέο κανόνα) χωρίς νέα σάρωση, κρατώντας τις εγκρίσεις."""
        if not (self.scan_result and self.model):
            return
        state = self.model.export_state()
        plan = build_plan(self.scan_result, self.report, self.cfg, extra_rules=self.overrides)
        self.model = PreviewModel(plan)
        self.model.import_state(state)
        self.render()

    # ---- πίνακας ----
    def render(self) -> None:
        self.tree.delete(*self.tree.get_children())
        if not self.model:
            self.refresh_buttons()
            return
        root = self.model.root

        def rel(x: Path) -> str:
            try:
                return str(x.relative_to(root))
            except ValueError:
                return str(x)

        for r in self.model.rows:
            p = r.proposal
            st = self.model.status(r.number)
            tags = []
            if st == "removed":
                tags.append("removed")
            elif st == "approved":
                tags.append("approved")
            elif p.confidence != SURE:
                tags.append("check")
            self.tree.insert("", "end", iid=str(r.number), tags=tuple(tags), values=(
                r.number, STATUS_LABELS[st], "Σίγουρη" if p.confidence == SURE else "Να τη δεις",
                rel(p.entry.path), rel(p.dest), f"[{p.rule}] {p.reason}"))
        self.refresh_buttons()
        self.show_detail()

    def selected_numbers(self) -> list[int]:
        return [int(i) for i in self.tree.selection()]

    def refresh_buttons(self) -> None:
        m = self.model
        if m:
            sure = [r for r in m.sure_rows if r.number not in m.removed and r.number not in m.approved]
            self.btn_sure.configure(text=f"Έγκριση όλων των σίγουρων ({len(sure)})",
                                    state="disabled" if self.busy else "normal")
            n = len(m.approved_rows)
            self.btn_go.configure(text=f"Μετακίνηση εγκεκριμένων ({n})…",
                                  state="normal" if n and not self.busy else "disabled")
            if not self.busy:
                text = (f"{len(m.rows)} προτάσεις: {len(m.sure_rows)} σίγουρες, {len(m.check_rows)} «να τις δεις». "
                        f"Εγκεκριμένες: {n}. Αφαιρεμένες: {len(m.removed)}.") if m.rows else \
                    "Δεν υπάρχει τίποτα να μετακινηθεί: όλα μένουν στη θέση τους."
                self.status.set(text + (f"  ·  {self.note}" if self.note else ""))
        else:
            self.btn_sure.configure(text="Έγκριση όλων των σίγουρων", state="disabled")
            self.btn_go.configure(text="Μετακίνηση εγκεκριμένων…", state="disabled")

    def show_detail(self, _evt=None) -> None:
        sel = self.selected_numbers()
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        if self.model and sel:
            p = next(r.proposal for r in self.model.rows if r.number == sel[0])
            self.detail.insert("1.0", f"Λόγος [{p.rule}]\n{p.reason}\n\nΒεβαιότητα: {confidence_line(p)}")
        self.detail.configure(state="disabled")

    # ---- έγκριση / αφαίρεση ----
    def approve_all_sure(self) -> None:
        if self.model and not self.busy:
            n = self.model.approve_all_sure()
            self.note = f"Εγκρίθηκαν {n} σίγουρες. Οι «να τις δεις» περιμένουν τη δική σου απόφαση."
            self.render()

    def approve_selected_rows(self) -> None:
        if self.model and not self.busy:
            self.model.approve_numbers(self.selected_numbers())
            self.render()

    def restore_selected(self) -> None:
        if self.model and not self.busy:
            self.model.restore(self.selected_numbers())
            self.render()

    def remove_selected(self) -> None:
        if not self.model or self.busy:
            return
        nums = self.selected_numbers()
        if not nums:
            return
        rows = [r for r in self.model.rows if r.number in nums]
        self.model.remove(nums)
        self.render()
        self.offer_rules(rows)

    # ---- μάθηση από τις διορθώσεις ----
    def offer_rules(self, rows) -> None:
        """Μετά την αφαίρεση: «να το κάνω κανόνα;». Ο κανόνας λέει ότι τέτοια αρχεία μένουν στη θέση τους."""
        made = False
        for r in rows:
            path = r.proposal.entry.path
            conds = learning.suggest_conditions(path, self.model.root)
            if not conds:
                continue
            choice = self.ask_rule_choice(
                f"Αφαίρεσες το «{path.name}». Να γίνει κανόνας, ώστε τέτοια αρχεία να μένουν στη θέση τους;", conds)
            if choice is SKIP_ALL:
                break
            if choice is not None:
                made = self.add_rule(choice, {"type": "keep"}) or made
        if made:
            self.replan()

    def add_rule(self, cond: learning.Condition, then: dict) -> bool:
        when = {"type": cond.type, "value": cond.value}
        if learning.has_rule(self.cfg.learned_rules, when, then):
            return False
        rule = learning.new_rule(self.cfg.learned_rules, when, then)
        self.cfg.learned_rules.append(rule)
        try:
            cfgmod.save(self.cfg, self.config_path)
        except (cfgmod.ConfigError, OSError) as exc:
            self.cfg.learned_rules.remove(rule)
            messagebox.showerror("Σφάλμα", f"Δεν αποθηκεύτηκε ο κανόνας: {exc}")
            return False
        self.note = "Νέος κανόνας " + learning.describe_rule(rule)
        return True

    def ask_rule_choice(self, question: str, conds: list[learning.Condition]):
        """Παράθυρο επιλογής. Επιστρέφει Condition, None («όχι, μόνο αυτή τη φορά») ή SKIP_ALL."""
        win = tk.Toplevel(self.root)
        win.title("Να το κάνω κανόνα;")
        win.transient(self.root)
        result: list = [None]
        ttk.Label(win, text=question, wraplength=520, padding=10).pack(anchor="w")
        var = tk.IntVar(value=-1)
        for i, c in enumerate(conds):
            ttk.Radiobutton(win, text=c.label, variable=var, value=i).pack(anchor="w", padx=16, pady=2)
        ttk.Label(win, text="Ο κανόνας αποθηκεύεται μόνο σε αυτόν τον υπολογιστή και μπορείς να τον σβήσεις "
                            "από το «Κανόνες που έμαθε…».", wraplength=520, padding=10).pack(anchor="w")

        def done(value) -> None:
            result[0] = value
            win.destroy()

        row = ttk.Frame(win, padding=10)
        row.pack(fill="x")
        ttk.Button(row, text="Ναι, φτιάξε κανόνα", command=lambda: done(conds[var.get()]) if var.get() >= 0 else None).pack(side="left")
        ttk.Button(row, text="Όχι, μόνο αυτή τη φορά", command=lambda: done(None)).pack(side="left", padx=8)
        ttk.Button(row, text="Όχι σε όλα", command=lambda: done(SKIP_ALL)).pack(side="left")
        win.protocol("WM_DELETE_WINDOW", lambda: done(None))
        win.grab_set()
        self.root.wait_window(win)
        return result[0]

    def ask_topic(self) -> str | None:
        """Παράθυρο επιλογής θέματος: από τη λίστα ή νέο όνομα."""
        win = tk.Toplevel(self.root)
        win.title("Αλλαγή θέματος")
        win.transient(self.root)
        result: list = [None]
        ttk.Label(win, text="Σε ποιο θέμα ανήκει; Διάλεξε από τη λίστα ή γράψε νέο όνομα φακέλου.",
                  wraplength=420, padding=10).pack(anchor="w")
        topics = [r["topic"] for r in self.cfg.topic_rules]
        for r in self.cfg.learned_rules:
            if r["then"]["type"] == "topic" and r["then"]["value"] not in topics:
                topics.append(r["then"]["value"])
        var = tk.StringVar()
        box = ttk.Combobox(win, textvariable=var, values=topics, width=40)
        box.pack(padx=10, pady=4)

        def done() -> None:
            name = var.get().strip()
            if not cfgmod._is_simple_name(name):
                messagebox.showerror("Αλλαγή θέματος", "Γράψε απλό όνομα φακέλου, χωρίς / \\ : * ? \" < > |", parent=win)
                return
            result[0] = name
            win.destroy()

        row = ttk.Frame(win, padding=10)
        row.pack(fill="x")
        ttk.Button(row, text="Εντάξει", command=done).pack(side="left")
        ttk.Button(row, text="Άκυρο", command=win.destroy).pack(side="left", padx=8)
        win.grab_set()
        self.root.wait_window(win)
        return result[0]

    def change_topic(self) -> None:
        if not self.model or self.busy:
            return
        sel = self.selected_numbers()
        rows = [r for r in self.model.rows if r.number in sel]
        if len(rows) != 1:
            messagebox.showinfo("Αλλαγή θέματος", "Διάλεξε ακριβώς ένα έγγραφο από τον πίνακα.")
            return
        p = rows[0].proposal
        if p.entry.kind not in learning.DOC_KINDS:
            messagebox.showinfo("Αλλαγή θέματος", "Το θέμα αλλάζει μόνο στα έγγραφα (Word, PDF, Excel). "
                                                  "Για τα υπόλοιπα μπορείς να αφαιρέσεις τη γραμμή.")
            return
        topic = self.ask_topic()
        if not topic:
            return
        then = {"type": "topic", "value": topic}
        conds = learning.suggest_conditions(p.entry.path, self.model.root)
        choice = self.ask_rule_choice(
            f"Το «{p.entry.path.name}» θα πάει στο θέμα «{topic}». Να γίνει κανόνας για τέτοια αρχεία;", conds) \
            if conds else None
        if choice is SKIP_ALL:
            choice = None
        if choice is not None:
            self.add_rule(choice, then)
        else:
            self._serial += 1
            self.overrides.append(learning.one_time_rule(p.entry.path, then, self._serial))
        self.replan()

    def open_rules(self) -> None:
        win = self.rules_win = tk.Toplevel(self.root)
        win.title("Κανόνες που έμαθε το πρόγραμμα")
        ttk.Label(win, text="Αυτοί οι κανόνες έγιναν από τις διορθώσεις σου και αποθηκεύονται μόνο σε αυτόν τον "
                            "υπολογιστή. Μπορείς να σβήσεις όποιον δεν θέλεις.", wraplength=620, padding=8).pack(anchor="w")
        self.rules_list = tk.Listbox(win, width=100, height=12, exportselection=False)
        self.rules_list.pack(fill="both", expand=True, padx=8)
        self._fill_rules()
        ttk.Button(win, text="Διαγραφή επιλεγμένου κανόνα…", command=self.delete_selected_rule).pack(pady=8)

    def _fill_rules(self) -> None:
        self.rules_list.delete(0, "end")
        self._rules_shown = sorted(self.cfg.learned_rules, key=lambda r: r["id"])
        for r in self._rules_shown:
            self.rules_list.insert("end", learning.describe_rule(r))
        if not self._rules_shown:
            self.rules_list.insert("end", "(δεν έχει μάθει κανέναν κανόνα ακόμη)")

    def delete_selected_rule(self) -> None:
        sel = self.rules_list.curselection()
        if not sel or not self._rules_shown or sel[0] >= len(self._rules_shown):
            return
        rule = self._rules_shown[sel[0]]
        if not messagebox.askyesno("Διαγραφή κανόνα", "Να σβηστεί αυτός ο κανόνας;\n\n" + learning.describe_rule(rule)):
            return
        self.cfg.learned_rules = [r for r in self.cfg.learned_rules if r["id"] != rule["id"]]
        try:
            cfgmod.save(self.cfg, self.config_path)
        except (cfgmod.ConfigError, OSError) as exc:
            self.cfg.learned_rules.append(rule)
            messagebox.showerror("Σφάλμα", f"Δεν αποθηκεύτηκε η αλλαγή: {exc}")
            return
        self._fill_rules()
        self.replan()

    # ---- μετακίνηση ----
    def confirm_and_move(self) -> None:
        if not self.model or self.busy:
            return
        rows = self.model.approved_rows
        if not rows:
            return
        reviews = sum(1 for r in rows if r.proposal.action == "review")
        manual = sum(1 for r in rows if r.proposal.confidence != SURE)
        if not messagebox.askyesno(
                "Έγκριση μετακίνησης",
                f"Θα μετακινηθούν {len(rows)} αρχεία ({reviews} στον φάκελο ελέγχου).\n"
                f"Από αυτά, {manual} ήταν «να τα δεις» και τα ενέκρινες εσύ.\n"
                "Δεν διαγράφεται τίποτα και θα γραφτεί manifest για αναίρεση.\n\nΣυνέχεια;"):
            return
        approved = self.model.approve_selected()
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
                    res, rep, plan = data
                    self.load_plan(plan, res, rep)
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
        self.note = ""
        self.scan_result = self.report = None  # τα αρχεία άλλαξαν θέση: χρειάζεται νέα σάρωση
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
