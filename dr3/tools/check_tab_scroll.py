"""Verify tab wheel-scrolling matches dr3_app.py's _scroll_tab behavior:

- content that overflows the fixed window gets a scrollbar and wheel events
  fire on every descendant (cards/rows), not just the bare canvas/inner;
- content that fits shows no scrollbar and wheel events do nothing.

Replicates dr3_app.py's layout + binding logic, generates <MouseWheel> events
on representative descendants, asserts scroll/no-scroll accordingly. Exits
non-zero on any failure.
"""
import sys
import tkinter as tk
from tkinter import ttk

BG = "#181C14"
BG_CARD = "#3C3D37"
BORDER = "#697565"


def build(parent, rows: int):
    outer = tk.Frame(parent, bg=BG)
    canvas = tk.Canvas(outer, bg=BG, highlightthickness=0, bd=0)
    sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=sb.set)
    inner = tk.Frame(canvas, bg=BG)
    win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.pack(side="left", fill="both", expand=True)

    def _sync_width(_e):
        canvas.itemconfigure(win_id, width=canvas.winfo_width())

    def _update_scrollbar():
        bbox = canvas.bbox("all")
        needs = bool(bbox) and bbox[3] > canvas.winfo_height() + 2
        if needs and not sb.winfo_ismapped():
            sb.pack(side="right", fill="y")
        elif not needs and sb.winfo_ismapped():
            sb.pack_forget()

    def _on_inner_cfg(_e):
        canvas.configure(scrollregion=canvas.bbox("all"))
        _sync_width(_e)
        _update_scrollbar()

    inner.bind("<Configure>", _on_inner_cfg)
    canvas.bind("<Configure>", _sync_width)

    def _wheel(event):
        if not sb.winfo_ismapped():
            return
        delta = getattr(event, "delta", 0) or \
            (120 if getattr(event, "num", 0) == 4 else
             -120 if getattr(event, "num", 0) == 5 else 0)
        if delta:
            canvas.yview_scroll(-1 if delta > 0 else 1, "units")

    for w in (canvas, inner):
        w.bind("<MouseWheel>", _wheel)
        w.bind("<Button-4>", _wheel)
        w.bind("<Button-5>", _wheel)

    card = tk.Frame(inner, bg=BG_CARD,
                    highlightbackground=BORDER, highlightthickness=1)
    card.pack(fill="x", padx=20, pady=10)
    title = tk.Label(card, text="Text Files", bg=BG_CARD)
    title.pack(anchor="w", padx=12, pady=(8, 4))
    cb = tk.Checkbutton(card,
                        text="Generate overlay files while server is running",
                        bg=BG_CARD)
    cb.pack(fill="x", padx=12, pady=(0, 6))
    spin = tk.Spinbox(card, from_=2, to=60, width=5, bg=BG)
    spin.pack(anchor="w", padx=12)
    deep_label = None
    for i in range(rows):
        row = tk.Frame(inner, bg=BG)
        row.pack(fill="x")
        lbl = tk.Label(row, text=f"row {i}", bg=BG)
        lbl.pack(side="left", padx=12, pady=2)
        if deep_label is None and i == min(5, rows - 1):
            deep_label = lbl

    def bind_wheel_everywhere():
        def _rec(w):
            w.bind("<MouseWheel>", _wheel)
            w.bind("<Button-4>", _wheel)
            w.bind("<Button-5>", _wheel)
            for c in w.winfo_children():
                _rec(c)
        _rec(inner)

    return outer, canvas, sb, inner, card, title, cb, spin, deep_label, \
        bind_wheel_everywhere


def main() -> int:
    root = tk.Tk()
    root.geometry("440x560+4000+3000")  # off-screen; still mapped
    root.resizable(False, False)
    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)

    failures = 0

    # --- Case 1: overflowing content (like the Overlay tab's card stack) ---
    (outer, canvas, sb, inner, card, title, cb, spin, deep_label,
     bind_wheel_everywhere) = build(notebook, rows=40)
    notebook.add(outer, text="Overlay")
    root.update_idletasks()
    root.update()

    if not (canvas.bbox("all") and canvas.bbox("all")[3] > canvas.winfo_height()):
        print("FAIL: overflow test content does not overflow the window")
        return 1
    bind_wheel_everywhere()
    root.update()
    if not sb.winfo_ismapped():
        print("FAIL: scrollbar not shown for overflowing tab")
        failures += 1

    targets = [
        ("canvas itself", canvas),
        ("inner frame (gap)", inner),
        ("card frame", card),
        ("section title label", title),
        ("checkbutton", cb),
        ("spinbox", spin),
        ("deep row label (2 levels down)", deep_label),
    ]
    for name, w in targets:
        before = canvas.yview()[1]
        w.event_generate("<MouseWheel>", delta=-120)
        root.update()
        after = canvas.yview()[1]
        if after > before:
            print(f"OK   wheel over {name}: scrolled {before:.3f} -> {after:.3f}")
        else:
            failures += 1
            print(f"FAIL wheel over {name}: no scroll (yview stayed {before:.3f})")

    # --- Case 2: fitting content (like Main/Logs) ---
    (outer2, canvas2, sb2, inner2, *_rest) = build(notebook, rows=0)
    notebook.add(outer2, text="Fits")
    root.update_idletasks()
    root.update()
    if sb2.winfo_ismapped():
        print("FAIL: scrollbar shown for a fitting tab")
        failures += 1
    else:
        print("OK   no scrollbar for fitting tab")
    before = canvas2.yview()[1]
    inner2.event_generate("<MouseWheel>", delta=-120)
    root.update()
    after = canvas2.yview()[1]
    if after == before and not sb2.winfo_ismapped():
        print("OK   wheel over fitting tab does nothing")
    else:
        failures += 1
        print(f"FAIL wheel over fitting tab moved the view "
              f"({before:.3f} -> {after:.3f})")

    root.destroy()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
