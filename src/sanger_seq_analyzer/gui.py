"""Desktop window (Tkinter, ships with Python on Windows and macOS)."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .pipeline import load_reference, run
from .qc import QCSettings
from .report import write_outputs


class App(ttk.Frame):
    def __init__(self, master: tk.Tk):
        super().__init__(master, padding=12)
        self.master = master
        self.events: queue.Queue = queue.Queue()
        self.report_path: Path | None = None
        d = QCSettings()

        self.input_dir = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.reference = tk.StringVar()
        self.cutoff = tk.DoubleVar(value=d.min_good_fraction * 100)
        self.min_q = tk.IntVar(value=d.min_quality)
        self.min_len = tk.IntVar(value=d.min_trimmed_length)
        self.mix = tk.DoubleVar(value=d.mixture_area * 100)
        self.call_mix = tk.BooleanVar(value=True)

        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)

        def path_row(row, label, var, command):
            ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Entry(self, textvariable=var).grid(row=row, column=1, sticky="ew", padx=6)
            ttk.Button(self, text="Browse…", command=command).grid(row=row, column=2)

        path_row(0, "AB1 folder", self.input_dir, self.pick_input)
        path_row(1, "Output folder", self.output_dir, self.pick_output)
        path_row(2, "Reference FASTA", self.reference, self.pick_reference)

        opts = ttk.LabelFrame(self, text="QC settings", padding=8)
        opts.grid(row=3, column=0, columnspan=3, sticky="ew", pady=8)
        for col, (label, var) in enumerate([
            ("Cutoff % good bases", self.cutoff), ("Good base ≥ Q", self.min_q),
            ("Min trimmed length", self.min_len), ("Mixture area %", self.mix),
        ]):
            ttk.Label(opts, text=label).grid(row=0, column=col * 2, sticky="w")
            ttk.Entry(opts, textvariable=var, width=7).grid(row=0, column=col * 2 + 1, padx=(4, 14))
        ttk.Checkbutton(opts, text="Call mixtures", variable=self.call_mix).grid(row=1, column=0, columnspan=3,
                                                                                  sticky="w", pady=(6, 0))
        ttk.Label(opts, text="Other RECall rules use the published defaults (see README / command line).",
                  foreground="#666").grid(row=1, column=3, columnspan=5, sticky="w", pady=(6, 0))

        buttons = ttk.Frame(self)
        buttons.grid(row=4, column=0, columnspan=3, sticky="ew")
        self.run_btn = ttk.Button(buttons, text="Run QC", command=self.start)
        self.run_btn.pack(side="left")
        self.open_btn = ttk.Button(buttons, text="Open report", command=self.open_report, state="disabled")
        self.open_btn.pack(side="left", padx=6)
        self.progress = ttk.Progressbar(buttons, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=6)

        cols = ("status", "length", "mixtures", "marks", "notes")
        self.table = ttk.Treeview(self, columns=cols, show="tree headings", height=12)
        self.table.heading("#0", text="Sample")
        for c, w in zip(cols, (70, 70, 70, 70, 420)):
            self.table.heading(c, text=c.capitalize())
            self.table.column(c, width=w, stretch=(c == "notes"))
        self.table.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=8)
        self.rowconfigure(5, weight=1)
        self.table.tag_configure("PASS", foreground="#1b5e20")
        self.table.tag_configure("FAIL", foreground="#b71c1c")

        self.status = ttk.Label(self, text="Choose a folder of .ab1 files.")
        self.status.grid(row=6, column=0, columnspan=3, sticky="w")

    def pick_input(self):
        d = filedialog.askdirectory(title="Folder with .ab1 files")
        if d:
            self.input_dir.set(d)
            if not self.output_dir.get():
                self.output_dir.set(str(Path(d) / "sanger_results"))

    def pick_output(self):
        d = filedialog.askdirectory(title="Output folder")
        if d:
            self.output_dir.set(d)

    def pick_reference(self):
        f = filedialog.askopenfilename(title="Reference FASTA",
                                       filetypes=[("FASTA", "*.fasta *.fa *.fas *.fna"), ("All files", "*")])
        if f:
            self.reference.set(f)

    def start(self):
        if not self.input_dir.get() or not Path(self.input_dir.get()).is_dir():
            messagebox.showerror("Sanger QC", "Choose a folder containing .ab1 files.")
            return
        if not self.reference.get() or not Path(self.reference.get()).is_file():
            messagebox.showerror("Sanger QC", "Choose the reference FASTA to assemble against.")
            return
        try:
            settings = QCSettings(
                min_quality=int(self.min_q.get()),
                min_good_fraction=float(self.cutoff.get()) / 100,
                min_trimmed_length=int(self.min_len.get()),
                mixture_area=float(self.mix.get()) / 100,
                call_mixtures=bool(self.call_mix.get()),
            )
        except (tk.TclError, ValueError):
            messagebox.showerror("Sanger QC", "QC settings must be numbers.")
            return
        out = self.output_dir.get() or str(Path(self.input_dir.get()) / "sanger_results")
        self.output_dir.set(out)
        self.run_btn.configure(state="disabled")
        self.open_btn.configure(state="disabled")
        self.table.delete(*self.table.get_children())
        threading.Thread(target=self._work, args=(settings, out), daemon=True).start()
        self.after(100, self._poll)

    def _work(self, settings, out):
        try:
            ref = load_reference(self.reference.get())
            result = run([self.input_dir.get()], ref, settings,
                         progress=lambda n, t, name: self.events.put(("progress", n, t, name)))
            paths = write_outputs(result, out)
            self.events.put(("done", result, paths))
        except Exception as exc:
            self.events.put(("error", exc))

    def _poll(self):
        try:
            while True:
                ev = self.events.get_nowait()
                if ev[0] == "progress":
                    _, n, total, name = ev
                    self.progress.configure(maximum=total, value=n)
                    self.status.configure(text=f"Analysing {name} ({n}/{total})…")
                elif ev[0] == "done":
                    self._show(ev[1], ev[2])
                    return
                elif ev[0] == "error":
                    self.run_btn.configure(state="normal")
                    self.status.configure(text="Failed.")
                    messagebox.showerror("Sanger QC", str(ev[1]))
                    return
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _show(self, result, paths):
        self.run_btn.configure(state="normal")
        for s in result.samples:
            c = s.consensus
            self.table.insert("", "end", text=s.sample, tags=(c.status,), values=(
                c.status, c.length, c.mixtures, c.marks, "; ".join(c.reasons)))
        if not result.samples:
            self.status.configure(text="No forward/reverse .ab1 pairs found (names need _F / _R tags).")
            return
        self.report_path = paths.get("html")
        self.open_btn.configure(state="normal" if self.report_path else "disabled")
        n_pass = sum(1 for s in result.samples if s.consensus.status == "PASS")
        self.status.configure(text=f"{n_pass}/{len(result.samples)} samples passed. Results in {paths['consensus'].parent}")

    def open_report(self):
        if self.report_path:
            webbrowser.open(self.report_path.resolve().as_uri())


def main() -> None:
    root = tk.Tk()
    root.title(f"Sanger Seq Analyzer {__version__}")
    root.geometry("900x560")
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
