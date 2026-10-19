from __future__ import annotations

import os
import hashlib
import json
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

import yaml

from ..settings import load_settings


class SyncGui:
    def __init__(self, root, project_root: Path, config_dir: Path | None, config_file: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self.tk = tk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.ttk = ttk
        self.window = root
        self.project_root = project_root.resolve()
        self.config_dir = config_dir
        self.config_file = (self.project_root / config_file).resolve() if config_file else self.project_root / "configuracion.yaml"
        self.output_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.dry_run_ok = False
        self.validated_fingerprint: str | None = None
        self.pending_fingerprint: str | None = None

        self.window.title("Basket Aranjuez - WordPress Sync 2.0")
        self.window.geometry("1000x850")
        self.window.minsize(760, 560)

        try:
            self.settings = load_settings(self.project_root, config_dir, self.config_file)
        except Exception as exc:
            self.settings = None
            self._show_startup_error(str(exc))
            return

        self.docx_var = tk.StringVar(value=self._default_docx())
        self.photos_var = tk.StringVar(value=self._default_photos_zip())
        self.has_photos_var = tk.BooleanVar(value=self.settings.runtime.photos_zip_available)
        self.weekend_var = tk.StringVar(value="")
        self.url_vars: dict[str, tk.StringVar] = {
            section: tk.StringVar(value=self.settings.runtime.section_entry_urls.get(section, ""))
            for section in self.settings.configured_sections()
        }
        self.section_vars = {section: tk.BooleanVar(value=bool(variable.get())) for section, variable in self.url_vars.items()}
        self.status_var = tk.StringVar(value="Listo para comprobar la jornada.")

        self._build_layout()
        for variable in (self.docx_var, self.photos_var, self.has_photos_var, self.weekend_var, *self.url_vars.values(), *self.section_vars.values()):
            variable.trace_add("write", self._invalidate_validation)
        self.window.after(100, self._drain_queue)

    def _show_startup_error(self, message: str) -> None:
        self.window.after(100, lambda: self.messagebox.showerror("Configuracion no valida", message))

    def _default_docx(self) -> str:
        path = self.settings.runtime.default_docx_path
        return str(path) if path else ""

    def _default_photos_zip(self) -> str:
        path = self.settings.runtime.photos_zip_path
        return str(path) if path else ""

    def _build_layout(self) -> None:
        tk = self.tk
        ttk = self.ttk
        main = ttk.Frame(self.window, padding=14)
        main.pack(fill="both", expand=True)

        ttk.Label(main, text="Actualizacion semanal de WordPress", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(
            main,
            text="Selecciona los archivos, comprueba el dry-run y actualiza solo despues de confirmarlo.",
        ).pack(anchor="w", pady=(2, 12))
        self.discovery_button = ttk.Button(main, text="Inspeccionar temporada (solo lectura)", command=self._start_discovery)
        self.discovery_button.pack(anchor="w", pady=(0, 10))
        self.automatic_button = ttk.Button(main, text="Comprobar y actualizar la jornada", command=self._start_automatic)
        self.automatic_button.pack(anchor="w", pady=(0, 10))
        discovery = ttk.Frame(main)
        discovery.pack(fill="x", pady=(0, 8))
        ttk.Label(discovery, text="Sabado de la jornada (AAAA-MM-DD)").pack(side="left")
        ttk.Entry(discovery, textvariable=self.weekend_var, width=14).pack(side="left", padx=8)
        self.week_button = ttk.Button(discovery, text="Buscar borradores de esta jornada", command=self._start_week_discovery)
        self.week_button.pack(side="left")

        files = ttk.LabelFrame(main, text="Archivos de esta jornada", padding=10)
        files.pack(fill="x", pady=(0, 10))
        self._file_row(files, "Word de cronicas", self.docx_var, self._choose_docx, 0)
        photo_row = ttk.Frame(files)
        photo_row.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Checkbutton(
            photo_row,
            text="Hay ZIP de fotos esta semana",
            variable=self.has_photos_var,
            command=self._photos_toggle,
        ).pack(side="left")
        ttk.Entry(photo_row, textvariable=self.photos_var, width=70).pack(side="left", padx=8, fill="x", expand=True)
        ttk.Button(photo_row, text="Buscar...", command=self._choose_photos).pack(side="left")
        files.columnconfigure(1, weight=1)

        urls = ttk.LabelFrame(main, text="URLs de las entradas", padding=10)
        urls.pack(fill="x", pady=(0, 10))
        for row, section in enumerate(self.url_vars):
            ttk.Checkbutton(urls, text=section, variable=self.section_vars[section], width=24).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Entry(urls, textvariable=self.url_vars[section]).grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=2)
        urls.columnconfigure(1, weight=1)

        actions = ttk.Frame(main)
        actions.pack(fill="x", pady=(0, 8))
        self.auth_button = ttk.Button(actions, text="1. Iniciar sesion", command=self._start_auth)
        self.auth_button.pack(side="left")
        self.login_done_button = ttk.Button(actions, text="Guardar sesion", command=self._finish_auth, state="disabled")
        self.login_done_button.pack(side="left", padx=6)
        ttk.Button(actions, text="Guardar configuracion", command=self._save_config).pack(side="left", padx=6)
        self.dry_button = ttk.Button(actions, text="2. Comprobar dry-run", command=self._start_dry_run)
        self.dry_button.pack(side="left", padx=6)
        self.update_button = ttk.Button(actions, text="3. Actualizar WordPress", command=self._start_update, state="disabled")
        self.update_button.pack(side="right")

        status = ttk.Frame(main)
        status.pack(fill="x", pady=(0, 6))
        ttk.Label(status, textvariable=self.status_var).pack(side="left")
        ttk.Button(status, text="Abrir carpeta de logs", command=self._open_logs).pack(side="right")

        output_frame = ttk.LabelFrame(main, text="Actividad", padding=6)
        output_frame.pack(fill="both", expand=True)
        self.output = tk.Text(output_frame, wrap="word", height=18, state="disabled", font=("Consolas", 9))
        scrollbar = ttk.Scrollbar(output_frame, orient="vertical", command=self.output.yview)
        self.output.configure(yscrollcommand=scrollbar.set)
        self.output.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _file_row(self, parent, label: str, variable, callback: Callable[[], None], row: int) -> None:
        ttk = self.ttk
        ttk.Label(parent, text=label, width=20).grid(row=row, column=0, sticky="w")
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=8)
        ttk.Button(parent, text="Buscar...", command=callback).grid(row=row, column=2)

    def _photos_toggle(self) -> None:
        state = "normal" if self.has_photos_var.get() else "disabled"
        for child in self.window.winfo_children():
            self._set_photo_entries(child, state)

    def _set_photo_entries(self, widget, state: str) -> None:
        if isinstance(widget, self.tk.Entry) and widget.cget("textvariable") == str(self.photos_var):
            widget.configure(state=state)
        for child in widget.winfo_children():
            self._set_photo_entries(child, state)

    def _choose_docx(self) -> None:
        path = self.filedialog.askopenfilename(
            title="Selecciona el Word de cronicas",
            filetypes=[("Documentos Word", "*.docx"), ("Todos los archivos", "*.*")],
        )
        if path:
            self.docx_var.set(path)
            try:
                from ..discovery import infer_weekend_from_docx

                self.weekend_var.set(infer_weekend_from_docx(Path(path)).isoformat())
            except (OSError, ValueError):
                self.weekend_var.set("")
            self.dry_run_ok = False
            self.update_button.configure(state="disabled")

    def _choose_photos(self) -> None:
        path = self.filedialog.askopenfilename(
            title="Selecciona el ZIP de fotos",
            filetypes=[("ZIP", "*.zip"), ("Todos los archivos", "*.*")],
        )
        if path:
            self.photos_var.set(path)
            self.has_photos_var.set(True)

    def _save_config(self) -> bool:
        payload = {
            "site_key": self.settings.runtime.default_site_key,
            "docx_path": self._config_path(self.docx_var.get()),
            "photos_zip_available": bool(self.has_photos_var.get()),
            "photos_zip_path": self._config_path(self.photos_var.get()) if self.photos_var.get() else "",
            "entry_urls": {section: variable.get().strip() for section, variable in self.url_vars.items()},
            "section_order": list(self.url_vars),
        }
        target = self.config_file
        try:
            if target.exists():
                existing = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
                if not isinstance(existing, dict):
                    raise ValueError("configuracion.yaml debe contener un mapa YAML.")
                payload = {**existing, **payload}
            target.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        except (OSError, ValueError, yaml.YAMLError) as exc:
            self.messagebox.showerror("No se pudo guardar", str(exc))
            return False
        self._append_output(f"Configuracion guardada en {target}\n")
        return True

    def _config_path(self, value: str) -> str:
        if not value:
            return ""
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.project_root / path
        try:
            return os.path.relpath(path.resolve(), self.project_root)
        except ValueError:
            return str(path)

    def _runner_command(self, *arguments: str) -> list[str]:
        options = ["--config-file", str(self.config_file)]
        if self.config_dir:
            options.extend(["--config-dir", str(self.config_dir.resolve())])
        if getattr(sys, "frozen", False):
            return [sys.executable, *options, *arguments]
        return [sys.executable, str(self.project_root / "wordpress_sync.py"), *options, *arguments]

    def _start_auth(self) -> None:
        self._start_process(
            self._runner_command("auth-login", "--site-key", self.settings.runtime.default_site_key),
            mode="auth",
        )
        self.login_done_button.configure(state="normal")

    def _finish_auth(self) -> None:
        if self.process and self.process.stdin:
            try:
                self.process.stdin.write("\n")
                self.process.stdin.flush()
                self.login_done_button.configure(state="disabled")
            except OSError as exc:
                self._append_output(f"No se pudo guardar la sesion: {exc}\n")

    def _start_automatic(self) -> None:
        self._start_dry_run(automatic=True)

    def _start_dry_run(self, *, automatic: bool = False) -> None:
        sections = [name for name, variable in self.section_vars.items() if variable.get()]
        if not sections:
            self.messagebox.showwarning("Elige las secciones", "Marca al menos una seccion para esta jornada.")
            return
        if not self._save_config():
            return
        docx = self.docx_var.get().strip()
        if not docx:
            self.messagebox.showwarning("Falta el Word", "Selecciona el Word de cronicas antes de continuar.")
            return
        self.dry_run_ok = False
        self.pending_fingerprint = self._input_fingerprint()
        self.update_button.configure(state="disabled")
        self._start_process(self._runner_command("dry-run-all", "--docx", docx, "--sections", *sections), mode="auto-check" if automatic else "dry-run")

    def _start_discovery(self) -> None:
        self._start_process(self._runner_command("discover-season"), mode="discovery")

    def _start_week_discovery(self) -> None:
        from datetime import date

        try:
            if not self.weekend_var.get().strip():
                from ..discovery import infer_weekend_from_docx

                self.weekend_var.set(infer_weekend_from_docx(Path(self.docx_var.get())).isoformat())
            weekend = date.fromisoformat(self.weekend_var.get().strip())
            if weekend.weekday() != 5:
                raise ValueError("La fecha debe ser un sabado.")
        except ValueError as exc:
            self.messagebox.showwarning("Fecha de jornada", f"Introduce el sabado en formato AAAA-MM-DD. {exc}")
            return
        self._invalidate_validation()
        self._start_process(self._runner_command("discover-week", "--weekend", weekend.isoformat()), mode="week-discovery")

    def _invalidate_validation(self, *_args) -> None:
        self.dry_run_ok = False
        self.validated_fingerprint = None
        self.update_button.configure(state="disabled")

    def _input_fingerprint(self) -> str:
        digest = hashlib.sha256()
        for variable in (self.docx_var, self.photos_var, self.has_photos_var, *self.url_vars.values(), *self.section_vars.values()):
            digest.update(repr(variable.get()).encode("utf-8"))
            digest.update(b"\0")
        paths = [self.docx_var.get(), str(self.config_file)]
        if self.has_photos_var.get():
            paths.append(self.photos_var.get())
        config_root = self.config_dir or self.project_root / "config"
        paths.extend(str(config_root / name) for name in ("teams.yaml", "runtime.yaml", "selectors.yaml"))
        paths.extend(str(path) for path in sorted((config_root / "seasons").glob("*/teams.yaml")))
        for value in paths:
            path = Path(value)
            if not path.is_absolute():
                path = self.project_root / path
            try:
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError:
                digest.update(b"missing")
        return digest.hexdigest()

    def _start_update(self) -> None:
        if not self.dry_run_ok:
            return
        if self.validated_fingerprint != self._input_fingerprint():
            self._invalidate_validation()
            self.messagebox.showwarning("La jornada ha cambiado", "Repite la comprobacion: se han cambiado archivos o configuracion.")
            return
        if not self.messagebox.askyesno(
            "Confirmar actualizacion",
            "El dry-run no tiene errores. Se abriran las entradas, se guardara una copia de configuracion y se guardaran cambios sin publicar. ¿Continuar?",
        ):
            return
        if not self._save_config():
            return
        self._start_process(
            self._runner_command("update-all", "--docx", self.docx_var.get().strip(), "--yes", "--sections", *[name for name, var in self.section_vars.items() if var.get()]),
            mode="update",
        )

    def _start_process(self, command: list[str], *, mode: str) -> None:
        if self.process and self.process.poll() is None:
            self.messagebox.showinfo("Proceso en curso", "Espera a que termine la operacion actual.")
            return
        self._append_output(f"$ {' '.join(command)}\n\n")
        self.status_var.set("Ejecutando... no cierres esta ventana.")
        self._set_action_state("disabled")
        try:
            self.process = subprocess.Popen(
                command,
                cwd=self.project_root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            self._set_action_state("normal")
            self.status_var.set("No se pudo iniciar el proceso.")
            self.messagebox.showerror("Error al iniciar", str(exc))
            return
        threading.Thread(target=self._read_process_output, args=(self.process, mode), daemon=True).start()

    def _read_process_output(self, process: subprocess.Popen[str], mode: str) -> None:
        if process.stdout:
            for line in process.stdout:
                self.output_queue.put(("line", line))
        code = process.wait()
        self.output_queue.put(("done", (mode, code)))

    def _drain_queue(self) -> None:
        try:
            while True:
                event, value = self.output_queue.get_nowait()
                if event == "line":
                    self._append_output(str(value))
                else:
                    mode, code = value
                    self._process_finished(str(mode), int(code))
        except queue.Empty:
            pass
        self.window.after(100, self._drain_queue)

    def _process_finished(self, mode: str, code: int) -> None:
        self._set_action_state("normal")
        if mode == "auth":
            self.login_done_button.configure(state="disabled")
        if mode in {"dry-run", "auto-check"} and code == 0:
            if self.pending_fingerprint == self._input_fingerprint():
                self.dry_run_ok = True
                self.validated_fingerprint = self.pending_fingerprint
                self.update_button.configure(state="normal")
            else:
                self._invalidate_validation()
                self._append_output("Los archivos cambiaron durante la comprobacion. Vuelve a comprobar.\n")
        if mode == "update":
            self._invalidate_validation()
        if mode == "week-discovery" and code == 0:
            try:
                path = self.project_root / self.settings.runtime.output_dir / "week_discovery.json"
                report = json.loads(path.read_text(encoding="utf-8"))
                if report["weekend"] != self.weekend_var.get().strip():
                    raise ValueError("La fecha del informe no coincide con la jornada seleccionada.")
                for section, variable in self.url_vars.items():
                    url = report["entry_urls"].get(section, "")
                    variable.set(url)
                    self.section_vars[section].set(bool(url))
                self._append_output("Borradores cargados. Las secciones publicadas o ambiguas quedan desmarcadas.\n")
            except (OSError, ValueError, KeyError) as exc:
                self._append_output(f"No se pueden cargar los borradores: {exc}\n")
        self.status_var.set("Operacion completada correctamente." if code == 0 else "Operacion detenida con errores.")
        self._append_output(f"\nProceso terminado con codigo {code}.\n")
        if mode == "auto-check" and self.dry_run_ok:
            self.window.after(0, self._start_update)

    def _set_action_state(self, state: str) -> None:
        self.auth_button.configure(state=state)
        self.dry_button.configure(state=state)
        self.discovery_button.configure(state=state)
        self.automatic_button.configure(state=state)
        self.week_button.configure(state=state)
        self.update_button.configure(state="normal" if state == "normal" and self.dry_run_ok else "disabled")

    def _append_output(self, value: str) -> None:
        self.output.configure(state="normal")
        self.output.insert("end", value)
        self.output.see("end")
        self.output.configure(state="disabled")

    def _open_logs(self) -> None:
        path = (self.project_root / self.settings.runtime.logs_dir).resolve()
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(path)
        except AttributeError:
            subprocess.Popen(["xdg-open", str(path)])


def run_gui(project_root: Path, config_dir: Path | None = None, config_file: Path | None = None) -> None:
    try:
        import tkinter as tk
    except ImportError as exc:
        raise RuntimeError("Python se instalo sin Tkinter. Usa el instalador oficial de Python para Windows.") from exc

    window = tk.Tk()
    SyncGui(window, project_root, config_dir, config_file)
    window.mainloop()
