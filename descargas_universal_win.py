#!/usr/bin/env python3
from __future__ import annotations

import calendar
import csv
import json
import os
import re
import subprocess
import sys
import threading
import tkinter as tk
from collections import Counter
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk
from urllib.parse import unquote, urljoin, urlparse

URL = "https://consultajurisprudencial.ramajudicial.gov.co/WebRelatoria/csj/index.xhtml"
YEARS = [2024, 2025, 2026]


def resource_path(relative_path: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative_path


def ensure_playwright_installed() -> None:
    # En ejecutable Windows (PyInstaller), se usa Playwright embebido.
    if getattr(sys, "frozen", False):
        bundled = resource_path("ms-playwright")
        if bundled.exists():
            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(bundled))
        return

    try:
        import playwright  # noqa: F401
    except Exception:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright"])
    subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])


def slugify(name: str) -> str:
    safe = re.sub(r"[^\w.\-]+", "_", name.strip(), flags=re.UNICODE).strip("._")
    return safe or "documento.pdf"


def summarize_log(log_csv: Path) -> tuple[int, int, Counter[str]]:
    ok_total, error_total, prefixes = 0, 0, Counter()
    if not log_csv.exists():
        return ok_total, error_total, prefixes
    with log_csv.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            state = (row.get("estado") or "").strip().upper()
            name = (row.get("archivo") or "").strip()
            if state == "OK":
                ok_total += 1
                prefix = re.split(r"\d", name, maxsplit=1)[0].strip("-_ ").upper()
                prefixes[prefix or "OTRO"] += 1
            elif state == "ERROR":
                error_total += 1
    return ok_total, error_total, prefixes


def write_summary(output_dir: Path, log_csv: Path) -> None:
    ok_total, error_total, prefixes = summarize_log(log_csv)
    ordered = dict(sorted(prefixes.items(), key=lambda it: (-it[1], it[0])))
    txt = output_dir / "resumen_descargas.txt"
    js = output_dir / "resumen_descargas.json"
    lines = [f"OK: {ok_total}", f"ERROR: {error_total}", "POR PREFIJO:"]
    lines.extend([f"{k}: {v}" for k, v in ordered.items()] or ["(sin datos)"])
    txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    js.write_text(
        json.dumps({"ok": ok_total, "error": error_total, "por_prefijo": ordered}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def run_download_month(year: int, month: int, output_root: Path) -> None:
    ensure_playwright_installed()
    from playwright.sync_api import Error, Locator, Page, TimeoutError, sync_playwright

    last_day = calendar.monthrange(year, month)[1]
    fecha_inicio = f"01/{month:02d}/{year}"
    fecha_fin = f"{last_day:02d}/{month:02d}/{year}"
    output_dir = output_root / f"descargas_{year}_{month:02d}"
    output_dir.mkdir(parents=True, exist_ok=True)
    log_csv = output_dir / "descargas_log.csv"
    with log_csv.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["indice", "estado", "archivo", "detalle"])

    def click_if_possible(locator: Locator) -> None:
        try:
            locator.click(timeout=2500)
        except Exception:
            try:
                locator.click(force=True, timeout=2500)
            except Exception:
                pass

    def fill_date(page: Page, selector: str, date_value: str) -> None:
        page.evaluate(
            """([css, value]) => {
                const el = document.querySelector(css);
                if (!el) return false;
                el.value = value;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('keyup', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
                return true;
            }""",
            [selector, date_value],
        )

    def select_sala_civil(page: Page) -> None:
        click_if_possible(page.locator("[id='searchForm:scivil'] .ui-selectcheckboxmenu-trigger").first)
        click_if_possible(page.locator("label[for='searchForm:scivil:0']").first)
        click_if_possible(page.locator("body"))
        page.evaluate(
            """() => {
                const cb = document.getElementById('searchForm:scivil:0');
                if (!cb) return false;
                cb.checked = true;
                cb.dispatchEvent(new Event('input', { bubbles: true }));
                cb.dispatchEvent(new Event('change', { bubbles: true }));
                cb.dispatchEvent(new Event('click', { bubbles: true }));
                return true;
            }"""
        )

    def select_tutelas(page: Page) -> None:
        click_if_possible(page.locator("label[for='searchForm:tutelaselect:1']").first)
        page.evaluate(
            """() => {
                const rd = document.getElementById('searchForm:tutelaselect:1');
                if (!rd) return false;
                rd.checked = true;
                rd.dispatchEvent(new Event('input', { bubbles: true }));
                rd.dispatchEvent(new Event('change', { bubbles: true }));
                rd.dispatchEvent(new Event('click', { bubbles: true }));
                return true;
            }"""
        )

    def get_current_result_key(page: Page) -> str:
        try:
            return (page.locator("[id='resultForm:pagText2']").first.inner_text(timeout=1200) or "").strip()
        except Exception:
            return ""

    def click_next_result(page: Page) -> bool:
        for sel in ["[id='resultForm:j_idt217']", "[id$='j_idt217']", ".ui-paginator-next"]:
            loc = page.locator(sel).first
            try:
                if loc.count() == 0:
                    continue
                cls = (loc.get_attribute("class", timeout=1000) or "").lower()
                if "disabled" in cls:
                    continue
                loc.click(timeout=5000)
                return True
            except Exception:
                continue
        return False

    def move_next_distinct(page: Page, current_key: str) -> bool:
        for _ in range(3):
            if not click_next_result(page):
                continue
            if current_key:
                try:
                    page.wait_for_function(
                        """(prev) => {
                            const el = document.getElementById('resultForm:pagText2');
                            return !!el && el.textContent && el.textContent.trim() !== prev;
                        }""",
                        arg=current_key,
                        timeout=9000,
                    )
                    return True
                except Exception:
                    pass
            try:
                page.wait_for_timeout(300)
            except Exception:
                pass
        return False

    def save_download(page: Page, context, idx: int) -> Path | None:
        pdf_link = page.get_by_role("link", name="Pdf").first
        try:
            href = (pdf_link.get_attribute("href", timeout=3000) or "").strip()
        except Exception:
            href = ""
        if href and not href.lower().startswith("javascript"):
            full = urljoin(page.url, href)
            try:
                resp = context.request.get(full, timeout=30000)
                if resp.ok and resp.body().startswith(b"%PDF"):
                    n = Path(unquote(urlparse(full).path)).name or f"documento_{idx:03d}.pdf"
                    fn = slugify(n if n.lower().endswith(".pdf") else f"{n}.pdf")
                    dst = output_dir / fn
                    if dst.exists():
                        dst = output_dir / f"{dst.stem}_{idx:03d}{dst.suffix}"
                    dst.write_bytes(resp.body())
                    return dst
            except Exception:
                pass
        try:
            with page.expect_download(timeout=12000) as dli:
                pdf_link.click()
            d = dli.value
            fn = slugify(d.suggested_filename or f"documento_{idx:03d}.pdf")
            dst = output_dir / fn
            if dst.exists():
                dst = output_dir / f"{dst.stem}_{idx:03d}{dst.suffix}"
            d.save_as(dst)
            return dst
        except Exception:
            return None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.goto(URL, wait_until="domcontentloaded")

        select_sala_civil(page)
        select_tutelas(page)
        try:
            col = page.locator("[id='searchForm:set-fecha_collapsed']").input_value()
            if col == "true":
                click_if_possible(page.locator("[id='searchForm:set-fecha'] legend").first)
        except Exception:
            pass
        fill_date(page, "[id='searchForm:fechaIniCal']", fecha_inicio)
        fill_date(page, "[id='searchForm:fechaFinCal']", fecha_fin)
        print(f"Fechas aplicadas: {fecha_inicio} - {fecha_fin}")
        page.get_by_role("button", name="Buscar", exact=True).click()
        page.wait_for_load_state("networkidle")

        idx, total_ok = 1, 0
        while True:
            key = get_current_result_key(page)
            click_if_possible(page.locator("[id='resultForm:jurisTable:0:display']").first)
            menu = page.locator("[id='resultForm:j_idt234_menuButton']").first
            if menu.count() == 0:
                menu = page.locator("[id$='menuButton']").first
            click_if_possible(menu)

            saved = save_download(page, context, idx)
            with log_csv.open("a", newline="", encoding="utf-8") as f:
                wr = csv.writer(f)
                if saved:
                    total_ok += 1
                    print(f"[{idx}] Descargado: {saved.name}")
                    wr.writerow([idx, "OK", saved.name, ""])
                else:
                    print(f"[{idx}] Sin descarga PDF disponible.")
                    wr.writerow([idx, "ERROR", "", "Sin PDF o timeout"])

            pos_txt = get_current_result_key(page)
            m = re.search(r"(\d+)\s*/\s*(\d+)", pos_txt)
            if m and int(m.group(1)) >= int(m.group(2)):
                print("Ultimo resultado alcanzado. Fin del proceso.")
                break
            if not move_next_distinct(page, key):
                print("No hay siguiente resultado para continuar.")
                break
            idx += 1

        print(f"Total descargados: {total_ok}")
        print(f"Log CSV: {log_csv}")
        write_summary(output_dir, log_csv)
        print(f"Resumen TXT: {output_dir / 'resumen_descargas.txt'}")
        print(f"Resumen JSON: {output_dir / 'resumen_descargas.json'}")
        context.close()
        browser.close()


class App:
    def __init__(self, master: tk.Tk) -> None:
        self.master = master
        self.master.title("Centro de Descargas Jurisprudenciales")
        self.master.geometry("1200x820")
        self.master.minsize(980, 680)
        self.master.resizable(True, True)
        self.style = ttk.Style(self.master)
        self.light_mode = tk.BooleanVar(value=False)

        self.running = False
        self.queue: list[tuple[int, int]] = []
        self.stop_requested = False
        self.month_buttons: dict[tuple[int, int], ttk.Button] = {}
        self.year_buttons: dict[int, ttk.Button] = {}

        self._configure_theme()
        self._build()

    def _configure_theme(self) -> None:
        self.style.theme_use("clam")
        self.style.configure(
            "Primary.TButton",
            borderwidth=0,
            padding=(12, 9),
            font=("SF Pro Text", 10, "bold"),
        )
        self.style.configure(
            "Month.TButton",
            borderwidth=1,
            padding=(10, 7),
            font=("SF Pro Text", 10),
        )
        self.style.configure(
            "Danger.TButton",
            borderwidth=0,
            padding=(12, 9),
            font=("SF Pro Text", 10, "bold"),
        )
        self._apply_theme()

    def _colors(self) -> dict[str, str]:
        if self.light_mode.get():
            return {
                "app_bg": "#f1f5f9",
                "panel_bg": "#ffffff",
                "header_fg": "#0f172a",
                "sub_fg": "#475569",
                "section_fg": "#0284c7",
                "status_fg": "#334155",
                "year_fg": "#0f172a",
                "primary_bg": "#0284c7",
                "primary_fg": "#ecfeff",
                "primary_active": "#0ea5e9",
                "primary_dis_bg": "#cbd5e1",
                "primary_dis_fg": "#94a3b8",
                "month_bg": "#e2e8f0",
                "month_fg": "#0f172a",
                "month_active": "#cbd5e1",
                "month_dis_bg": "#e2e8f0",
                "month_dis_fg": "#94a3b8",
                "danger_bg": "#b91c1c",
                "danger_fg": "#fee2e2",
                "danger_active": "#dc2626",
                "danger_dis_bg": "#cbd5e1",
                "danger_dis_fg": "#94a3b8",
                "check_fg": "#334155",
                "log_bg": "#f8fafc",
                "log_fg": "#0f172a",
                "log_cursor": "#0284c7",
                "progress_bg": "#06b6d4",
                "progress_trough": "#e2e8f0",
            }
        return {
            "app_bg": "#0b1220",
            "panel_bg": "#101a2c",
            "header_fg": "#e5e7eb",
            "sub_fg": "#94a3b8",
            "section_fg": "#38bdf8",
            "status_fg": "#cbd5e1",
            "year_fg": "#e2e8f0",
            "primary_bg": "#0ea5e9",
            "primary_fg": "#00131f",
            "primary_active": "#38bdf8",
            "primary_dis_bg": "#334155",
            "primary_dis_fg": "#94a3b8",
            "month_bg": "#1e293b",
            "month_fg": "#e2e8f0",
            "month_active": "#334155",
            "month_dis_bg": "#1f2937",
            "month_dis_fg": "#64748b",
            "danger_bg": "#dc2626",
            "danger_fg": "#fee2e2",
            "danger_active": "#ef4444",
            "danger_dis_bg": "#4b5563",
            "danger_dis_fg": "#9ca3af",
            "check_fg": "#cbd5e1",
            "log_bg": "#0f172a",
            "log_fg": "#dbeafe",
            "log_cursor": "#38bdf8",
            "progress_bg": "#22d3ee",
            "progress_trough": "#0f172a",
        }

    def _apply_theme(self) -> None:
        c = self._colors()
        self.master.configure(bg=c["app_bg"])
        self.style.configure("App.TFrame", background=c["app_bg"])
        self.style.configure("Panel.TFrame", background=c["panel_bg"])
        self.style.configure("Header.TLabel", background=c["app_bg"], foreground=c["header_fg"], font=("SF Pro Display", 22, "bold"))
        self.style.configure("SubHeader.TLabel", background=c["app_bg"], foreground=c["sub_fg"], font=("SF Pro Text", 11))
        self.style.configure("Section.TLabel", background=c["panel_bg"], foreground=c["section_fg"], font=("SF Pro Text", 10, "bold"))
        self.style.configure("Status.TLabel", background=c["panel_bg"], foreground=c["status_fg"], font=("SF Pro Text", 11))

        self.style.configure("YearCard.TLabelframe", background=c["panel_bg"], borderwidth=1, relief="solid")
        self.style.configure("YearCard.TLabelframe.Label", background=c["panel_bg"], foreground=c["year_fg"], font=("SF Pro Text", 11, "bold"))

        self.style.configure("Primary.TButton", background=c["primary_bg"], foreground=c["primary_fg"])
        self.style.map("Primary.TButton", background=[("active", c["primary_active"]), ("disabled", c["primary_dis_bg"])], foreground=[("disabled", c["primary_dis_fg"])])

        self.style.configure("Month.TButton", background=c["month_bg"], foreground=c["month_fg"])
        self.style.map("Month.TButton", background=[("active", c["month_active"]), ("disabled", c["month_dis_bg"])], foreground=[("disabled", c["month_dis_fg"])])

        self.style.configure("Danger.TButton", background=c["danger_bg"], foreground=c["danger_fg"])
        self.style.map("Danger.TButton", background=[("active", c["danger_active"]), ("disabled", c["danger_dis_bg"])], foreground=[("disabled", c["danger_dis_fg"])])

        self.style.configure("Theme.TCheckbutton", background=c["panel_bg"], foreground=c["check_fg"], font=("SF Pro Text", 10))
        self.style.map("Theme.TCheckbutton", foreground=[("active", c["section_fg"])])

        self.style.configure(
            "Tech.Horizontal.TProgressbar",
            troughcolor=c["progress_trough"],
            background=c["progress_bg"],
            bordercolor=c["progress_trough"],
            lightcolor=c["progress_bg"],
            darkcolor=c["progress_bg"],
        )
        if hasattr(self, "log"):
            self.log.configure(bg=c["log_bg"], fg=c["log_fg"], insertbackground=c["log_cursor"])

    def _toggle_theme(self) -> None:
        self._apply_theme()

    def _build(self) -> None:
        root = ttk.Frame(self.master, style="App.TFrame", padding=(16, 14))
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="Centro de Descargas Jurisprudenciales", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            root,
            text="Automatización mensual y anual de providencias (Sala Civil - Tutelas).",
            style="SubHeader.TLabel",
        ).pack(anchor="w", pady=(2, 12))

        panel = ttk.Frame(root, style="Panel.TFrame", padding=(12, 12))
        panel.pack(fill="both", expand=True)

        top = ttk.Frame(panel, style="Panel.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text="CONTROL DE EJECUCIÓN", style="Section.TLabel").pack(side="left")
        ttk.Checkbutton(
            top,
            text="Modo claro",
            variable=self.light_mode,
            style="Theme.TCheckbutton",
            command=self._toggle_theme,
        ).pack(side="right", padx=(0, 12))
        self.btn_stop = ttk.Button(
            top,
            text="Detener después del mes actual",
            style="Danger.TButton",
            state="disabled",
            command=self._stop,
        )
        self.btn_stop.pack(side="right")

        self.progress = ttk.Progressbar(panel, mode="indeterminate", style="Tech.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(10, 10))

        months = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        container = ttk.Frame(panel, style="Panel.TFrame")
        container.pack(fill="x", pady=2)
        for year in YEARS:
            lf = ttk.LabelFrame(container, text=f"AÑO {year}", style="YearCard.TLabelframe", padding=(10, 10))
            lf.pack(fill="x", pady=6)
            by = ttk.Button(
                lf,
                text=f"Descargar completo {year}",
                style="Primary.TButton",
                command=lambda y=year: self._run_year(y),
            )
            by.grid(row=0, column=0, columnspan=4, padx=6, pady=6, sticky="ew")
            self.year_buttons[year] = by
            for i, name in enumerate(months, start=1):
                b = ttk.Button(lf, text=name, style="Month.TButton", command=lambda y=year, m=i: self._run_month(y, m))
                b.grid(row=1 + (i - 1) // 4, column=(i - 1) % 4, padx=6, pady=6, sticky="ew")
                self.month_buttons[(year, i)] = b
            for c in range(4):
                lf.grid_columnconfigure(c, weight=1)

        self.status = tk.StringVar(value="Listo.")
        ttk.Label(panel, textvariable=self.status, style="Status.TLabel", anchor="w").pack(fill="x", pady=(10, 4))
        self.log = scrolledtext.ScrolledText(
            panel,
            wrap=tk.WORD,
            height=14,
            relief="flat",
            borderwidth=8,
            font=("SF Mono", 11),
        )
        self.log.pack(fill="both", expand=True, pady=(0, 2))
        self.log.insert(tk.END, "Aplicación iniciada.\n")
        self._apply_theme()

    def _append(self, text: str) -> None:
        self.log.insert(tk.END, text.rstrip() + "\n")
        self.log.see(tk.END)

    def _set_controls(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for b in self.month_buttons.values():
            b.configure(state=state)
        for b in self.year_buttons.values():
            b.configure(state=state)
        self.btn_stop.configure(state="normal" if running else "disabled")
        if running:
            self.progress.start(10)
        else:
            self.progress.stop()

    def _stop(self) -> None:
        self.stop_requested = True
        self.queue.clear()
        self.status.set("Se detendrá al finalizar el mes actual.")
        self._append("Solicitud de detener después del mes actual.")

    def _run_month(self, year: int, month: int) -> None:
        if self.running:
            return
        self.running = True
        self.stop_requested = False
        self.queue.clear()
        self._set_controls(True)
        threading.Thread(target=self._exec_month, args=(year, month), daemon=True).start()

    def _run_year(self, year: int) -> None:
        if self.running:
            return
        self.running = True
        self.stop_requested = False
        self.queue = [(year, m) for m in range(2, 13)]
        self._set_controls(True)
        threading.Thread(target=self._exec_month, args=(year, 1), daemon=True).start()

    def _exec_month(self, year: int, month: int) -> None:
        self.master.after(0, self.status.set, f"Descargando {month:02d}/{year}...")
        self.master.after(0, self._append, f"=== Inicio mes {month:02d}/{year} ===")
        cmd = [sys.executable, str(Path(__file__).resolve()), "--run-month", str(year), str(month)]
        proc = subprocess.Popen(cmd, cwd=str(Path(__file__).resolve().parent), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert proc.stdout is not None
        for line in proc.stdout:
            self.master.after(0, self._append, line.rstrip("\n"))
        rc = proc.wait()
        self.master.after(0, self._month_done, year, month, rc)

    def _month_done(self, year: int, month: int, rc: int) -> None:
        self._append(f"=== Fin mes {month:02d}/{year} ({'OK' if rc == 0 else f'ERROR {rc}'}) ===")
        if rc != 0:
            self.running = False
            self.queue.clear()
            self._set_controls(False)
            self.status.set(f"Error en {month:02d}/{year}.")
            messagebox.showerror("Error", f"Falló la descarga del mes {month:02d}/{year}.")
            return
        if self.queue and not self.stop_requested:
            y, m = self.queue.pop(0)
            threading.Thread(target=self._exec_month, args=(y, m), daemon=True).start()
            return
        self.running = False
        self.queue.clear()
        self._set_controls(False)
        self.status.set("Proceso completado.")


def main() -> None:
    if "--run-month" in sys.argv:
        idx = sys.argv.index("--run-month")
        year = int(sys.argv[idx + 1])
        month = int(sys.argv[idx + 2])
        run_download_month(year, month, Path.home())
        return
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
