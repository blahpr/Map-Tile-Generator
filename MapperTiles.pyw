import string
import re
import json
import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, legal, A3, A4, A5, landscape
from reportlab.lib.utils import ImageReader
from reportlab.lib.units import inch
from reportlab.lib import colors

# --- HIGH DPI AWARENESS (Fixes scaling issues on high-res monitors) ---
try:
    from ctypes import windll

    windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

Image.MAX_IMAGE_PIXELS = None

# --- DIRECTORY & PATH RESOLUTION ---
if getattr(sys, 'frozen', False):
    EXE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    EXE_DIR = os.path.dirname(os.path.abspath(__file__))

SETTINGS_DIR = os.path.join(EXE_DIR, "settings")
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "maptiler_settings.json")
OUTPUT_DIR = os.path.join(EXE_DIR, "pdf")


def get_next_map_tag(output_dir):
    """
    Scans the PDF output directory for existing tags like 1A, 1B... 1Z, 2A, etc.
    an returns next tag in seq.
    """
    if not os.path.exists(output_dir):
        return "1A"

    max_num = 1
    max_let_idx = -1
    found_any = False

    # Regex matching tags like _1A, _12B, etc. before .pdf extension
    pattern = re.compile(r'_(\d+)([A-Z])\.pdf$', re.IGNORECASE)

    for fname in os.listdir(output_dir):
        match = pattern.search(fname)
        if match:
            found_any = True
            num = int(match.group(1))
            let_idx = string.ascii_uppercase.index(match.group(2).upper())

            if (num > max_num) or (num == max_num and let_idx > max_let_idx):
                max_num = num
                max_let_idx = let_idx

    if not found_any:
        return "1A"

    # Advance letter A -> Z, then wrap to next number
    if max_let_idx < 25:
        next_num = max_num
        next_let_idx = max_let_idx + 1
    else:
        next_num = max_num + 1
        next_let_idx = 0

    next_letter = string.ascii_uppercase[next_let_idx]
    return f"{next_num}{next_letter}"

def ensure_directories():
    """Automatically recreates required folders next to the EXE if missing."""
    for folder in [SETTINGS_DIR, OUTPUT_DIR]:
        os.makedirs(folder, exist_ok=True)


ensure_directories()


def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller temporary extraction """
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = EXE_DIR
    return os.path.join(base_path, relative_path)


DEFAULT_SETTINGS = {
    "cols": 2,
    "rows": 2,
    "fit_mode": "crop",
    "margin": 0.25,
    "dpi": 300,
    "overlap": 0.25,
    "rotation": 0,
    "orientation": "portrait",
    "paper_size": "Letter",
    "custom_w": 8.5,
    "custom_h": 11.0,
    "letterbox_color": "Black",
    "show_headers": True,
    "show_guides": True,
    "disable_smoothing": False,
    "sidebar_width": 220,
    "pan_x": 0.0,
    "pan_y": 0.0,
}


class ScrollableFrame(ttk.Frame):
    def __init__(self, container, *args, **kwargs):
        super().__init__(container, *args, **kwargs)
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scrollable_content = ttk.Frame(self.canvas, padding=10)

        self.scrollable_content.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas_frame = self.canvas.create_window((0, 0), window=self.scrollable_content, anchor="nw")

        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfig(self.canvas_frame, width=e.width)
        )

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.scrollable_content.bind("<Enter>", self._bind_mousewheel)
        self.scrollable_content.bind("<Leave>", self._unbind_mousewheel)

    def _bind_mousewheel(self, event):
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self, event):
        self.canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


class MapTilerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Map Tile Generator v1.1               (By: Blahpr 2026)")
        self.geometry("1100x750")
        self.minsize(900, 600)

        self._apply_global_icon()

        # App State
        self.image_path = None
        self.src_image = None
        self.preview_tk_img = None

        # Performance & Smoothness Caching
        self.pyramid_cache = {}
        self.render_debounce_job = None
        self.is_dragging = False

        self.saved_defaults = self.load_settings_from_disk()

        # Tkinter Variables
        self.cols_var = tk.IntVar(value=self.saved_defaults.get("cols", 2))
        self.rows_var = tk.IntVar(value=self.saved_defaults.get("rows", 2))
        self.fit_mode_var = tk.StringVar(value=self.saved_defaults.get("fit_mode", "crop"))
        self.margin_var = tk.DoubleVar(value=self.saved_defaults.get("margin", 0.25))
        self.dpi_var = tk.IntVar(value=self.saved_defaults.get("dpi", 300))
        self.overlap_var = tk.DoubleVar(value=self.saved_defaults.get("overlap", 0.25))
        self.rotation_var = tk.IntVar(value=self.saved_defaults.get("rotation", 0))
        self.orientation_var = tk.StringVar(value=self.saved_defaults.get("orientation", "portrait"))
        self.paper_size_var = tk.StringVar(value=self.saved_defaults.get("paper_size", "Letter"))
        self.custom_w_var = tk.DoubleVar(value=self.saved_defaults.get("custom_w", 8.5))
        self.custom_h_var = tk.DoubleVar(value=self.saved_defaults.get("custom_h", 11.0))
        self.letterbox_color_var = tk.StringVar(value=self.saved_defaults.get("letterbox_color", "Black"))
        self.show_headers_var = tk.BooleanVar(value=self.saved_defaults.get("show_headers", True))
        self.show_guides_var = tk.BooleanVar(value=self.saved_defaults.get("show_guides", True))
        self.disable_smoothing_var = tk.BooleanVar(value=self.saved_defaults.get("disable_smoothing", False))   
        self.pan_x_var = tk.DoubleVar(value=self.saved_defaults.get("pan_x", 0.0))
        self.pan_y_var = tk.DoubleVar(value=self.saved_defaults.get("pan_y", 0.0))

        # Preview Zoom State
        self.zoom_level = 1.0
        self.pan_offset_x = 0.0
        self.pan_offset_y = 0.0
        self.drag_last_x = 0
        self.drag_last_y = 0

        self._build_ui()

    def _apply_global_icon(self):
        ico_path = resource_path(os.path.join("images", "p.ico"))
        png_path = resource_path(os.path.join("images", "app_icon.png"))

        if os.path.exists(ico_path):
            try:
                self.iconbitmap(ico_path)
            except Exception as e:
                print(f"Could not load .ico file: {e}")
        elif os.path.exists(png_path):
            try:
                img = Image.open(png_path)
                photo = ImageTk.PhotoImage(img)
                self.iconphoto(True, photo)
            except Exception as e:
                print(f"Could not load .png icon file: {e}")

    def load_settings_from_disk(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r") as f:
                    data = json.load(f)
                    defaults = DEFAULT_SETTINGS.copy()
                    defaults.update(data)
                    return defaults
            except Exception as e:
                print(f"Error loading settings file: {e}")
        return DEFAULT_SETTINGS.copy()

    def get_current_settings(self):
        return {
            "cols": self.cols_var.get(),
            "rows": self.rows_var.get(),
            "fit_mode": self.fit_mode_var.get(),
            "margin": self.margin_var.get(),
            "dpi": self.dpi_var.get(),
            "overlap": self.overlap_var.get(),
            "rotation": self.rotation_var.get(),
            "orientation": self.orientation_var.get(),
            "paper_size": self.paper_size_var.get(),
            "custom_w": self.custom_w_var.get(),
            "custom_h": self.custom_h_var.get(),
            "letterbox_color": self.letterbox_color_var.get(),
            "show_headers": self.show_headers_var.get(),
            "show_guides": self.show_guides_var.get(),
            "disable_smoothing": self.disable_smoothing_var.get(),
            "pan_x": self.pan_x_var.get(),
            "pan_y": self.pan_y_var.get(),
            "sash_pos": self.main_paned.sashpos(0) if hasattr(self, "main_paned") else self.saved_defaults.get("sash_pos", 320)
        }

    def apply_settings(self, settings_dict):
        self.cols_var.set(settings_dict.get("cols", 2))
        self.rows_var.set(settings_dict.get("rows", 2))
        self.fit_mode_var.set(settings_dict.get("fit_mode", "crop"))
        self.margin_var.set(settings_dict.get("margin", 0.25))
        self.dpi_var.set(settings_dict.get("dpi", 300))
        self.overlap_var.set(settings_dict.get("overlap", 0.25))
        self.rotation_var.set(settings_dict.get("rotation", 0))
        self.orientation_var.set(settings_dict.get("orientation", "portrait"))
        self.paper_size_var.set(settings_dict.get("paper_size", "Letter"))
        self.custom_w_var.set(settings_dict.get("custom_w", 8.5))
        self.custom_h_var.set(settings_dict.get("custom_h", 11.0))
        self.letterbox_color_var.set(settings_dict.get("letterbox_color", "Black"))
        self.show_headers_var.set(settings_dict.get("show_headers", True))
        self.show_guides_var.set(settings_dict.get("show_guides", True))
        self.disable_smoothing_var.set(settings_dict.get("disable_smoothing", False))
        self.pan_x_var.set(settings_dict.get("pan_x", 0.0))
        self.pan_y_var.set(settings_dict.get("pan_y", 0.0))
        self.toggle_custom_inputs()
        self.on_fit_mode_change()

    def save_current_as_default(self):
        settings = self.get_current_settings()
        try:
            os.makedirs(SETTINGS_DIR, exist_ok=True)
            with open(SETTINGS_FILE, "w") as f:
                json.dump(settings, f, indent=4)
            messagebox.showinfo(
                "Settings Saved",
                f"Current configuration saved to settings folder:\n{SETTINGS_FILE}",
            )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save settings: {e}")

    def reset_to_defaults(self):
        self.apply_settings(DEFAULT_SETTINGS)
        messagebox.showinfo("Settings Reset", "Restored to factory default settings.")

    def toggle_custom_inputs(self, event=None):
        if self.paper_size_var.get() == "Custom":
            self.custom_dim_frame.pack(fill=tk.X, pady=(2, 2))
        else:
            self.custom_dim_frame.pack_forget()
        self.update_preview()

    def _build_ui(self):
        self.main_paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        self.main_paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # --- LEFT CONTROL PANEL (Scrollable) ---
        ctrl_sidebar = ScrollableFrame(self.main_paned)
        self.main_paned.add(ctrl_sidebar, weight=0)
        ctrl_frame = ctrl_sidebar.scrollable_content

        settings_box = ttk.LabelFrame(ctrl_frame, text=" Settings Management ", padding=6)
        settings_box.pack(fill=tk.X, pady=(0, 5))
        
        btn_set_def = ttk.Button(settings_box, text="Save as Startup Default", command=self.save_current_as_default)
        btn_set_def.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))
        
        btn_reset_def = ttk.Button(settings_box, text="Reset Defaults", command=self.reset_to_defaults)
        btn_reset_def.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(2, 0))

        file_box = ttk.LabelFrame(ctrl_frame, text=" 1. Source Image ", padding=10)
        file_box.pack(fill=tk.X, pady=(0, 10))

        self.btn_browse = ttk.Button(file_box, text="Select Map Image...", command=self.browse_image)
        self.btn_browse.pack(fill=tk.X)
        self.lbl_file = ttk.Label(file_box, text="No image selected", font=("Helvetica", 8, "italic"), wraplength=280)
        self.lbl_file.pack(anchor=tk.W, pady=(5, 0))

        grid_box = ttk.LabelFrame(ctrl_frame, text=" 2. Grid & Page Setup ", padding=10)
        grid_box.pack(fill=tk.X, pady=(0, 10))

        paper_frame = ttk.Frame(grid_box)
        paper_frame.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(paper_frame, text="Paper Size:   ").pack(side=tk.LEFT)
        paper_cb = ttk.Combobox(
            paper_frame,
            textvariable=self.paper_size_var,
            values=["Letter", "Legal", "A3", "A4", "A5", "Custom"],
            state="readonly",
            width=10
        )
        paper_cb.pack(side=tk.LEFT)

        self.custom_dim_frame = ttk.Frame(grid_box)
        ttk.Label(self.custom_dim_frame, text="Width (in):").pack(side=tk.LEFT, padx=(0, 2))
        ent_cw = ttk.Entry(self.custom_dim_frame, textvariable=self.custom_w_var, width=5)
        ent_cw.pack(side=tk.LEFT, padx=(0, 5))

        ttk.Label(self.custom_dim_frame, text="Height (in):").pack(side=tk.LEFT, padx=(0, 2))
        ent_ch = ttk.Entry(self.custom_dim_frame, textvariable=self.custom_h_var, width=5)
        ent_ch.pack(side=tk.LEFT)

        paper_cb.bind("<<ComboboxSelected>>", self.toggle_custom_inputs)
        ent_cw.bind("<FocusOut>", lambda e: self.update_preview())
        ent_ch.bind("<FocusOut>", lambda e: self.update_preview())

        grid_inner = ttk.Frame(grid_box)
        grid_inner.pack(fill=tk.X)

        ttk.Label(grid_inner, text="Columns (X):").grid(row=0, column=0, sticky=tk.W, pady=2)
        spn_cols = ttk.Spinbox(grid_inner, from_=1, to=12, textvariable=self.cols_var, width=5, command=self.update_preview)
        spn_cols.grid(row=0, column=1, sticky=tk.E, pady=2)

        ttk.Label(grid_inner, text="Rows (Y):").grid(row=1, column=0, sticky=tk.W, pady=2)
        spn_rows = ttk.Spinbox(grid_inner, from_=1, to=12, textvariable=self.rows_var, width=5, command=self.update_preview)
        spn_rows.grid(row=1, column=1, sticky=tk.E, pady=2)

        orient_frame = ttk.Frame(grid_box)
        orient_frame.pack(fill=tk.X, pady=(6, 2))
        ttk.Label(orient_frame, text="Orientation:").pack(side=tk.LEFT)
        ttk.Radiobutton(orient_frame, text="Portrait", value="portrait", variable=self.orientation_var, command=self.update_preview).pack(side=tk.LEFT, padx=(8, 2))
        ttk.Radiobutton(orient_frame, text="Landscape", value="landscape", variable=self.orientation_var, command=self.update_preview).pack(side=tk.LEFT, padx=2)

        preset_frame = ttk.Frame(grid_box)
        preset_frame.pack(fill=tk.X, pady=(5, 0))

        row_2 = ttk.Frame(preset_frame)
        row_2.pack(fill=tk.X, pady=1)
        ttk.Label(row_2, width=3).pack(side=tk.LEFT)
        ttk.Button(row_2, text="2x2", width=4, command=lambda: self.set_grid(2, 2)).pack(side=tk.LEFT, padx=1)
        ttk.Button(row_2, text="2x3", width=4, command=lambda: self.set_grid(2, 3)).pack(side=tk.LEFT, padx=1)
        ttk.Button(row_2, text="2x4", width=4, command=lambda: self.set_grid(2, 4)).pack(side=tk.LEFT, padx=1)

        row_3 = ttk.Frame(preset_frame)
        row_3.pack(fill=tk.X, pady=1)
        ttk.Label(row_3, width=3).pack(side=tk.LEFT)
        ttk.Button(row_3, text="3x2", width=4, command=lambda: self.set_grid(3, 2)).pack(side=tk.LEFT, padx=1)
        ttk.Button(row_3, text="3x3", width=4, command=lambda: self.set_grid(3, 3)).pack(side=tk.LEFT, padx=1)
        ttk.Button(row_3, text="3x4", width=4, command=lambda: self.set_grid(3, 4)).pack(side=tk.LEFT, padx=1)

        row_4 = ttk.Frame(preset_frame)
        row_4.pack(fill=tk.X, pady=1)
        ttk.Label(row_4, width=3).pack(side=tk.LEFT)
        ttk.Button(row_4, text="4x2", width=4, command=lambda: self.set_grid(4, 2)).pack(side=tk.LEFT, padx=1)
        ttk.Button(row_4, text="4x3", width=4, command=lambda: self.set_grid(4, 3)).pack(side=tk.LEFT, padx=1)
        ttk.Button(row_4, text="4x4", width=4, command=lambda: self.set_grid(4, 4)).pack(side=tk.LEFT, padx=1)

        row_5 = ttk.Frame(preset_frame)
        row_5.pack(fill=tk.X, pady=1)
        ttk.Label(row_2, width=1).pack(side=tk.RIGHT)
        ttk.Button(row_2, text="5x4", width=4, command=lambda: self.set_grid(5, 4)).pack(side=tk.RIGHT, padx=1)
        ttk.Button(row_2, text="5x3", width=4, command=lambda: self.set_grid(5, 3)).pack(side=tk.RIGHT, padx=1)
        ttk.Button(row_2, text="5x2", width=4, command=lambda: self.set_grid(5, 2)).pack(side=tk.RIGHT, padx=1)

        ow_3 = ttk.Frame(preset_frame)
        row_3.pack(fill=tk.X, pady=1)
        ttk.Label(row_3, width=1).pack(side=tk.RIGHT)
        ttk.Button(row_3, text="6x4", width=4, command=lambda: self.set_grid(6, 4)).pack(side=tk.RIGHT, padx=1)
        ttk.Button(row_3, text="6x3", width=4, command=lambda: self.set_grid(6, 3)).pack(side=tk.RIGHT, padx=1)
        ttk.Button(row_3, text="6x2", width=4, command=lambda: self.set_grid(6, 2)).pack(side=tk.RIGHT, padx=1)

        ow_4 = ttk.Frame(preset_frame)
        row_4.pack(fill=tk.X, pady=1)
        ttk.Label(row_4, width=1).pack(side=tk.RIGHT)
        ttk.Button(row_4, text="2x1", width=4, command=lambda: self.set_grid(2, 1)).pack(side=tk.RIGHT, padx=1)
        ttk.Button(row_4, text="1x2", width=4, command=lambda: self.set_grid(1, 2)).pack(side=tk.RIGHT, padx=1)
        ttk.Button(row_4, text="1x1", width=4, command=lambda: self.set_grid(1, 1)).pack(side=tk.RIGHT, padx=1)

        fit_box = ttk.LabelFrame(ctrl_frame, text=" 3. Image Fitting & Alignment ", padding=10)
        fit_box.pack(fill=tk.X, pady=(0, 10))

        ttk.Radiobutton(fit_box, text="Crop to Fit (Preserve Aspect Ratio)", value="crop", variable=self.fit_mode_var, command=self.on_fit_mode_change).pack(anchor=tk.W)
        ttk.Radiobutton(fit_box, text="Contain (Letterbox / Full Map Visible)", value="contain", variable=self.fit_mode_var, command=self.on_fit_mode_change).pack(anchor=tk.W)
        ttk.Radiobutton(fit_box, text="Stretch to Fit (100% Fill)", value="stretch", variable=self.fit_mode_var, command=self.on_fit_mode_change).pack(anchor=tk.W, pady=(0, 5))

        self.lb_color_frame = ttk.Frame(fit_box)
        self.lb_color_frame.pack(fill=tk.X, padx=5, pady=(0, 6))
        ttk.Label(self.lb_color_frame, text="Letterbox Color:").pack(side=tk.LEFT)
        lb_cb = ttk.Combobox(self.lb_color_frame, textvariable=self.letterbox_color_var, values=["Black", "White", "Dark Slate", "Light Gray"], state="readonly", width=12)
        lb_cb.pack(side=tk.RIGHT)
        lb_cb.bind("<<ComboboxSelected>>", lambda e: self.update_preview())

        self.pan_frame = ttk.Frame(fit_box)
        self.pan_frame.pack(fill=tk.X, padx=5)

        ttk.Label(self.pan_frame, text="Horizontal Alignment Offset:").pack(anchor=tk.W)
        h_frame = ttk.Frame(self.pan_frame)
        h_frame.pack(fill=tk.X)
        ttk.Button(h_frame, text="L", width=2, command=lambda: self.set_alignment(-1.0, None)).pack(side=tk.LEFT)
        self.slider_pan_x = ttk.Scale(h_frame, from_=-1.0, to=1.0, variable=self.pan_x_var, command=lambda e: self.update_preview())
        self.slider_pan_x.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        ttk.Button(h_frame, text="C", width=2, command=lambda: self.set_alignment(0.0, None)).pack(side=tk.LEFT)
        ttk.Button(h_frame, text="R", width=2, command=lambda: self.set_alignment(1.0, None)).pack(side=tk.LEFT)

        ttk.Label(self.pan_frame, text="Vertical Alignment Offset:").pack(anchor=tk.W, pady=(4, 0))
        v_frame = ttk.Frame(self.pan_frame)
        v_frame.pack(fill=tk.X)
        ttk.Button(v_frame, text="T", width=2, command=lambda: self.set_alignment(None, -1.0)).pack(side=tk.LEFT)
        self.slider_pan_y = ttk.Scale(v_frame, from_=-1.0, to=1.0, variable=self.pan_y_var, command=lambda e: self.update_preview())
        self.slider_pan_y.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        ttk.Button(v_frame, text="C", width=2, command=lambda: self.set_alignment(None, 0.0)).pack(side=tk.LEFT)
        ttk.Button(v_frame, text="B", width=2, command=lambda: self.set_alignment(None, 1.0)).pack(side=tk.LEFT)

        rot_frame = ttk.Frame(fit_box)
        rot_frame.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(rot_frame, text="Rotate Map:").pack(side=tk.LEFT)
        ttk.Radiobutton(rot_frame, text="0°", value=0, variable=self.rotation_var, command=self.update_preview).pack(side=tk.LEFT, padx=(5, 2))
        ttk.Radiobutton(rot_frame, text="90°", value=90, variable=self.rotation_var, command=self.update_preview).pack(side=tk.LEFT, padx=2)
        ttk.Radiobutton(rot_frame, text="180°", value=180, variable=self.rotation_var, command=self.update_preview).pack(side=tk.LEFT, padx=2)
        ttk.Radiobutton(rot_frame, text="270°", value=270, variable=self.rotation_var, command=self.update_preview).pack(side=tk.LEFT, padx=2)

        print_box = ttk.LabelFrame(ctrl_frame, text=" 4. Resolution & Overlap ", padding=10)
        print_box.pack(fill=tk.X, pady=(0, 10))

        dpi_frame = ttk.Frame(print_box)
        dpi_frame.pack(fill=tk.X)
        ttk.Label(dpi_frame, text="Target Quality:").pack(side=tk.LEFT)
        ttk.Radiobutton(dpi_frame, text="300 DPI", value=300, variable=self.dpi_var).pack(side=tk.LEFT, padx=(10, 5))
        ttk.Radiobutton(dpi_frame, text="600 DPI", value=600, variable=self.dpi_var).pack(side=tk.LEFT)

        overlap_frame = ttk.Frame(print_box)
        overlap_frame.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(overlap_frame, text="Glue Overlap:").pack(side=tk.LEFT)
        ttk.Radiobutton(overlap_frame, text="0.25\"", value=0.25, variable=self.overlap_var, command=self.update_preview).pack(side=tk.LEFT, padx=(5, 2))
        ttk.Radiobutton(overlap_frame, text="0.50\"", value=0.50, variable=self.overlap_var, command=self.update_preview).pack(side=tk.LEFT, padx=2)
        ttk.Radiobutton(overlap_frame, text="0.75\"", value=0.75, variable=self.overlap_var, command=self.update_preview).pack(side=tk.LEFT, padx=2)

        margin_frame = ttk.Frame(print_box)
        margin_frame.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(margin_frame, text="Margins:").pack(side=tk.LEFT)
        ttk.Radiobutton(margin_frame, text="0.10\" Narrow", value=0.10, variable=self.margin_var, command=self.update_preview).pack(side=tk.LEFT, padx=(10, 5))
        ttk.Radiobutton(margin_frame, text="0.25\" Standard", value=0.25, variable=self.margin_var, command=self.update_preview).pack(side=tk.LEFT)

        ttk.Checkbutton(print_box, text="Show Sheet Headers / Labels", variable=self.show_headers_var).pack(anchor=tk.W, pady=(6, 0))
        ttk.Checkbutton(print_box, text="Show Overlap Guides & Crop Lines", variable=self.show_guides_var, command=self.update_preview).pack(anchor=tk.W)
        ttk.Checkbutton(print_box, text="Disable Smoothing (Preview & PDF)", variable=self.disable_smoothing_var, command=self.update_preview).pack(anchor=tk.W)

        self.btn_generate = ttk.Button(ctrl_frame, text="Generate PDF", command=self.generate_pdf, state=tk.DISABLED)
        self.btn_generate.pack(fill=tk.X, pady=(10, 0), ipady=8)

        # --- RIGHT PREVIEW PANEL ---
        preview_box = ttk.LabelFrame(self.main_paned, text=" Map Preview ", padding=10)
        self.main_paned.add(preview_box, weight=1)

        zoom_bar = ttk.Frame(preview_box)
        zoom_bar.pack(fill=tk.X, pady=(0, 5))

        ttk.Label(zoom_bar, text="Zoom:").pack(side=tk.LEFT, padx=(0, 5))
        self.zoom_lbl = ttk.Label(zoom_bar, text="100%", width=5)
        self.zoom_lbl.pack(side=tk.LEFT)

        ttk.Button(zoom_bar, text="Fit View", width=12, command=self.reset_view).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(zoom_bar, text="Zoom In (+)", width=12, command=lambda: self._apply_zoom_step(1.25)).pack(side=tk.RIGHT, padx=2)
        ttk.Button(zoom_bar, text="Zoom Out (-)", width=12, command=lambda: self._apply_zoom_step(0.8)).pack(side=tk.RIGHT, padx=2)

        self.canvas_preview = tk.Canvas(
            preview_box, 
            bg="#1a1a1a", 
            highlightthickness=0, 
            cursor="fleur"
        )
        self.canvas_preview.pack(fill=tk.BOTH, expand=True)

        self.canvas_preview.bind("<Configure>", lambda e: self.update_preview())
        self.canvas_preview.bind("<MouseWheel>", self._on_mouse_wheel)
        self.canvas_preview.bind("<Button-4>", self._on_mouse_wheel)
        self.canvas_preview.bind("<Button-5>", self._on_mouse_wheel)

        self.canvas_preview.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas_preview.bind("<B1-Motion>", self._on_drag_motion)
        self.canvas_preview.bind("<ButtonRelease-1>", self._on_drag_release)

        # Restore sash position after layout geometry settles
        saved_sash = self.saved_defaults.get("sash_pos", 320)
        self.after(100, lambda: self.main_paned.sashpos(0, saved_sash))

        # Save sash position on drag release
        self.main_paned.bind("<ButtonRelease-1>", lambda e: self.save_sash_position())

        self.toggle_custom_inputs()
        self.on_fit_mode_change()

    def save_sash_position(self):
        pos = self.main_paned.sashpos(0)
        if pos > 0:
            self.saved_defaults["sash_pos"] = pos

    def get_letterbox_rgb(self):
        c_map = {
            "Black": (0, 0, 0),
            "White": (255, 255, 255),
            "Dark Slate": (30, 30, 30),
            "Light Gray": (200, 200, 200)
        }
        return c_map.get(self.letterbox_color_var.get(), (0, 0, 0))

    def set_alignment(self, x_val, y_val):
        if x_val is not None:
            self.pan_x_var.set(x_val)
        if y_val is not None:
            self.pan_y_var.set(y_val)
        self.update_preview()

    def reset_view(self):
        self.zoom_level = 1.0
        self.pan_offset_x = 0.0
        self.pan_offset_y = 0.0
        self.zoom_lbl.config(text="100%")
        self.update_preview()

    def _build_image_pyramid(self, img):
        self.pyramid_cache.clear()
        if not img:
            return
        w, h = img.size
        self.pyramid_cache[1.0] = img
        for scale in [0.5, 0.25, 0.125]:
            nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
            self.pyramid_cache[scale] = img.resize((nw, nh), Image.Resampling.BILINEAR)

    def _get_cached_source_tier(self, target_scale):
        if not self.pyramid_cache:
            return self.get_processed_source_image()
        available_scales = sorted(self.pyramid_cache.keys())
        best_scale = available_scales[0]
        for s in available_scales:
            if s <= target_scale:
                best_scale = s
            else:
                break
        return self.pyramid_cache[best_scale]

    def _apply_zoom_step(self, factor):
        if not self.src_image:
            return
        c_x = self.canvas_preview.winfo_pointerx() - self.canvas_preview.winfo_rootx()
        c_y = self.canvas_preview.winfo_pointery() - self.canvas_preview.winfo_rooty()
        c_w = self.canvas_preview.winfo_width()
        c_h = self.canvas_preview.winfo_height()

        if 0 <= c_x <= c_w and 0 <= c_y <= c_h:
            focus_x, focus_y = c_x, c_y
        else:
            focus_x, focus_y = c_w / 2.0, c_h / 2.0

        self._zoom_at_point(factor, focus_x, focus_y)

    def _zoom_at_point(self, factor, focus_x, focus_y):
        new_zoom = max(0.5, min(10.0, self.zoom_level * factor))
        if new_zoom == self.zoom_level:
            return

        c_w = self.canvas_preview.winfo_width()
        c_h = self.canvas_preview.winfo_height()
        dims = self.calculate_dimensions()
        
        padding = 50
        avail_w = max(1.0, c_w - (2 * padding))
        avail_h = max(1.0, c_h - (2 * padding))
        base_scale = min(avail_w / dims["total_w"], avail_h / dims["total_h"])

        old_disp_w = dims["total_w"] * base_scale * self.zoom_level
        old_disp_h = dims["total_h"] * base_scale * self.zoom_level

        new_disp_w = dims["total_w"] * base_scale * new_zoom
        new_disp_h = dims["total_h"] * base_scale * new_zoom

        old_origin_x = ((c_w - old_disp_w) / 2.0) + self.pan_offset_x
        old_origin_y = ((c_h - old_disp_h) / 2.0) + self.pan_offset_y

        rel_x = (focus_x - old_origin_x) / old_disp_w if old_disp_w > 0 else 0.5
        rel_y = (focus_y - old_origin_y) / old_disp_h if old_disp_h > 0 else 0.5

        self.zoom_level = new_zoom

        new_base_origin_x = (c_w - new_disp_w) / 2.0
        new_base_origin_y = (c_h - new_disp_h) / 2.0

        self.pan_offset_x = focus_x - (rel_x * new_disp_w) - new_base_origin_x
        self.pan_offset_y = focus_y - (rel_y * new_disp_h) - new_base_origin_y

        self.zoom_lbl.config(text=f"{int(self.zoom_level * 100)}%")

        if self.render_debounce_job:
            self.after_cancel(self.render_debounce_job)

        self.update_preview(fast_mode=True)
        self.render_debounce_job = self.after(60, lambda: self.update_preview(fast_mode=False))

    def _on_mouse_wheel(self, event):
        if not self.src_image:
            return
        factor = 1.15 if (event.num == 4 or event.delta > 0) else 0.85
        self._zoom_at_point(factor, event.x, event.y)

    def _on_drag_start(self, event):
        if not self.src_image:
            return
        self.is_dragging = True
        self.drag_last_x = event.x
        self.drag_last_y = event.y

    def _on_drag_motion(self, event):
        if not self.src_image:
            return

        dx = event.x - self.drag_last_x
        dy = event.y - self.drag_last_y

        self.pan_offset_x += dx
        self.pan_offset_y += dy

        self.drag_last_x = event.x
        self.drag_last_y = event.y

        self.canvas_preview.move("all", dx, dy)

    def _on_drag_release(self, event):
        if not self.src_image:
            return
        self.is_dragging = False
        self.update_preview(fast_mode=False)

    def set_grid(self, cols, rows):
        self.cols_var.set(cols)
        self.rows_var.set(rows)
        self.update_preview()

    def on_fit_mode_change(self):
        fit_mode = self.fit_mode_var.get()
        state = tk.NORMAL if fit_mode in ("crop", "contain") else tk.DISABLED

        lb_state = tk.NORMAL if fit_mode == "contain" else tk.DISABLED
        for child in self.lb_color_frame.winfo_children():
            child.configure(state=lb_state)

        for child in self.pan_frame.winfo_children():
            if isinstance(child, ttk.Frame):
                for sub in child.winfo_children():
                    sub.configure(state=state)
            else:
                child.configure(state=state)
        self.update_preview()

    def browse_image(self):
        file_types = [("Image Files", "*.jpg *.jpeg *.png *.tif *.tiff"), ("All Files", "*.*")]
        path = filedialog.askopenfilename(title="Select Map Image", filetypes=file_types)
        if path:
            self.image_path = path
            self.src_image = Image.open(path).convert("RGB")
            self._build_image_pyramid(self.src_image)
            self.lbl_file.config(text=os.path.basename(path))
            self.btn_generate.config(state=tk.NORMAL)
            self.reset_view()

    def get_processed_source_image(self):
        if not self.src_image:
            return None
        rot = self.rotation_var.get()
        if rot == 90:
            return self.src_image.transpose(Image.ROTATE_270)
        elif rot == 180:
            return self.src_image.transpose(Image.ROTATE_180)
        elif rot == 270:
            return self.src_image.transpose(Image.ROTATE_90)
        return self.src_image

    def calculate_dimensions(self):
        cols, rows = self.cols_var.get(), self.rows_var.get()
        margin = self.margin_var.get()
        overlap = self.overlap_var.get()

        sizes = {
            "Letter": (8.5, 11.0),
            "Legal": (8.5, 14.0),
            "A3": (11.69, 16.54),
            "A4": (8.27, 11.69),
            "A5": (5.83, 8.27)
        }

        selected_paper = self.paper_size_var.get()
        if selected_paper == "Custom":
            base_w = self.custom_w_var.get()
            base_h = self.custom_h_var.get()
        else:
            base_w, base_h = sizes.get(selected_paper, (8.5, 11.0))

        if self.orientation_var.get() == "landscape":
            page_w, page_h = base_h, base_w
        else:
            page_w, page_h = base_w, base_h

        print_w = page_w - (2 * margin)
        print_h = page_h - (2 * margin)

        step_w = print_w - overlap
        step_h = print_h - overlap

        total_w = (cols * step_w) + overlap
        total_h = (rows * step_h) + overlap

        return {
            "cols": cols, "rows": rows, "margin": margin, "overlap": overlap,
            "page_w": page_w, "page_h": page_h,
            "print_w": print_w, "print_h": print_h,
            "step_w": step_w, "step_h": step_h,
            "total_w": total_w, "total_h": total_h
        }

    def update_preview(self, fast_mode=False):
        if fast_mode:
            img = self._get_cached_source_tier(self.zoom_level)
        else:
            img = self.get_processed_source_image()

        if not img:
            self.canvas_preview.delete("all")
            self.canvas_preview.create_text(
                self.canvas_preview.winfo_width() // 2,
                self.canvas_preview.winfo_height() // 2,
                text="Load an image to preview tile grid and alignment",
                fill="#666666", font=("Helvetica", 12)
            )
            return

        c_w = self.canvas_preview.winfo_width()
        c_h = self.canvas_preview.winfo_height()
        if c_w < 50 or c_h < 50:
            return

        dims = self.calculate_dimensions()
        cols, rows = dims["cols"], dims["rows"]
        total_w, total_h = dims["total_w"], dims["total_h"]

        padding = 50
        avail_w = c_w - (2 * padding)
        avail_h = c_h - (2 * padding)

        base_scale = min(avail_w / total_w, avail_h / total_h)
        scale = base_scale * self.zoom_level

        disp_w = max(1, int(total_w * scale))
        disp_h = max(1, int(total_h * scale))

        offset_x = ((c_w - disp_w) / 2.0) + self.pan_offset_x
        offset_y = ((c_h - disp_h) / 2.0) + self.pan_offset_y

        if self.disable_smoothing_var.get():
            resample_mode = Image.Resampling.NEAREST
        else:
            resample_mode = Image.Resampling.NEAREST if fast_mode else Image.Resampling.BILINEAR

        fit_mode = self.fit_mode_var.get()
        if fit_mode == "stretch":
            preview_img = img.resize((disp_w, disp_h), resample_mode)
        else:
            target_ratio = total_w / total_h
            im_w, im_h = img.width, img.height
            im_ratio = im_w / im_h

            if fit_mode == "crop":
                if im_ratio > target_ratio:
                    crop_w = int(im_h * target_ratio)
                    max_offset = (im_w - crop_w) / 2.0
                    pan_px = int(self.pan_x_var.get() * max_offset)
                    left = int((im_w - crop_w) / 2.0 + pan_px)
                    cropped = img.crop((left, 0, left + crop_w, im_h))
                else:
                    crop_h = int(im_w / target_ratio)
                    max_offset = (im_h - crop_h) / 2.0
                    pan_px = int(self.pan_y_var.get() * max_offset)
                    top = int((im_h - crop_h) / 2.0 + pan_px)
                    cropped = img.crop((0, top, im_w, top + crop_h))
                preview_img = cropped.resize((disp_w, disp_h), resample_mode)

            elif fit_mode == "contain":
                bg = Image.new("RGB", (disp_w, disp_h), self.get_letterbox_rgb())
                if im_ratio > target_ratio:
                    fit_w = disp_w
                    fit_h = max(1, int(disp_w / im_ratio))
                else:
                    fit_h = disp_h
                    fit_w = max(1, int(disp_h * im_ratio))

                resized = img.resize((fit_w, fit_h), resample_mode)
                pos_x = int((disp_w - fit_w) / 2.0 + (self.pan_x_var.get() * (disp_w - fit_w) / 2.0))
                pos_y = int((disp_h - fit_h) / 2.0 + (self.pan_y_var.get() * (disp_h - fit_h) / 2.0))
                bg.paste(resized, (pos_x, pos_y))
                preview_img = bg

        self.preview_tk_img = ImageTk.PhotoImage(preview_img)
        self.canvas_preview.delete("all")

        self.canvas_preview.create_image(offset_x, offset_y, anchor=tk.NW, image=self.preview_tk_img)

        step_w_px = dims["step_w"] * scale
        step_h_px = dims["step_h"] * scale
        print_w_px = dims["print_w"] * scale
        print_h_px = dims["print_h"] * scale
        overlap_px = dims["overlap"] * scale

        for r in range(rows):
            for c in range(cols):
                tile_x0 = offset_x + (c * step_w_px)
                tile_y0 = offset_y + (r * step_h_px)
                tile_x1 = tile_x0 + print_w_px
                tile_y1 = tile_y0 + print_h_px

                self.canvas_preview.create_rectangle(tile_x0, tile_y0, tile_x1, tile_y1, outline="#00e5ff", width=2)

                if self.show_guides_var.get():
                    if c < cols - 1:
                        seam_x = tile_x1 - overlap_px
                        self.canvas_preview.create_rectangle(seam_x, tile_y0, tile_x1, tile_y1, fill="#ff9100", stipple="gray25", outline="")
                    if r < rows - 1:
                        seam_y = tile_y1 - overlap_px
                        self.canvas_preview.create_rectangle(tile_x0, seam_y, tile_x1, tile_y1, fill="#ff9100", stipple="gray25", outline="")

                sheet_num = (r * cols) + c + 1
                self.canvas_preview.create_rectangle(tile_x0 + 6, tile_y0 + 6, tile_x0 + 72, tile_y0 + 24, fill="#121212", outline="#00e5ff")
                self.canvas_preview.create_text(tile_x0 + 39, tile_y0 + 15, text=f"Sheet {sheet_num}", fill="#ffffff", font=("Helvetica", 9, "bold"))

    def generate_pdf(self):
        img = self.get_processed_source_image()
        if not img or not self.image_path:
            return

        dims = self.calculate_dimensions()
        cols, rows = dims["cols"], dims["rows"]
        total_tiles = cols * rows
        margin = dims["margin"]
        overlap = dims["overlap"]
        dpi = self.dpi_var.get()
        page_w = dims["page_w"]
        page_h = dims["page_h"]

        ensure_directories()

        base_name = os.path.splitext(os.path.basename(self.image_path))[0]
        mode_str = self.fit_mode_var.get()
        orient_str = self.orientation_var.get()

        # Fetch the next sequential tag (e.g., 1A, 1B ... 1Z -> 2A)
        tag = get_next_map_tag(OUTPUT_DIR)

        # Output path using the sequence tag
        out_path = os.path.join(
            OUTPUT_DIR, 
            f"{base_name}_{mode_str.capitalize()}_{orient_str.capitalize()}_{cols}x{rows}_{dpi}DPI_{tag}.pdf")

        progress_win = tk.Toplevel(self)
        progress_win.title("Generating PDF...")
        progress_win.geometry("420x150")
        progress_win.resizable(False, False)
        progress_win.transient(self)
        progress_win.grab_set()

        try:
            icon_path = os.path.join(os.path.dirname(__file__), "images", "p.ico")
            progress_win.iconbitmap(icon_path)
        except Exception:
            pass

        p_x = self.winfo_x() + (self.winfo_width() // 2) - 210
        p_y = self.winfo_y() + (self.winfo_height() // 2) - 75
        progress_win.geometry(f"+{p_x}+{p_y}")

        lbl_status = ttk.Label(progress_win, text="Preparing canvas...", font=("Helvetica", 9))
        lbl_status.pack(pady=(20, 5), padx=20, anchor=tk.W)

        pbar = ttk.Progressbar(progress_win, orient=tk.HORIZONTAL, length=380, mode='determinate', maximum=total_tiles)
        pbar.pack(pady=5, padx=20)

        lbl_count = ttk.Label(progress_win, text=f"0 / {total_tiles} pages rendered", font=("Helvetica", 8, "italic"))
        lbl_count.pack(pady=(0, 15), padx=20, anchor=tk.E)

        def worker():
            px_w = round(dims["total_w"] * dpi)
            px_h = round(dims["total_h"] * dpi)

            # 1. Choose PIL resampling mode based on smoothing toggle
            if self.disable_smoothing_var.get():
                resample_filter = Image.Resampling.NEAREST
            else:
                resample_filter = Image.Resampling.LANCZOS

            if mode_str == "stretch":
                map_img = img.resize((px_w, px_h), resample_filter)
            elif mode_str == "crop":
                target_ratio = dims["total_w"] / dims["total_h"]
                im_w, im_h = img.width, img.height
                im_ratio = im_w / im_h

                if im_ratio > target_ratio:
                    crop_w = int(im_h * target_ratio)
                    max_offset = (im_w - crop_w) / 2.0
                    pan_px = int(self.pan_x_var.get() * max_offset)
                    left = int((im_w - crop_w) / 2.0 + pan_px)
                    cropped = img.crop((left, 0, left + crop_w, im_h))
                else:
                    crop_h = int(im_w / target_ratio)
                    max_offset = (im_h - crop_h) / 2.0
                    pan_px = int(self.pan_y_var.get() * max_offset)
                    top = int((im_h - crop_h) / 2.0 + pan_px)
                    cropped = img.crop((0, top, im_w, top + crop_h))
                map_img = cropped.resize((px_w, px_h), resample_filter)
            else:
                map_img = Image.new("RGB", (px_w, px_h), self.get_letterbox_rgb())
                target_ratio = dims["total_w"] / dims["total_h"]
                im_w, im_h = img.width, img.height
                im_ratio = im_w / im_h

                if im_ratio > target_ratio:
                    fit_w = px_w
                    fit_h = int(px_w / im_ratio)
                else:
                    fit_h = px_h
                    fit_w = int(px_h * im_ratio)

                resized = img.resize((fit_w, fit_h), resample_filter)
                pos_x = int((px_w - fit_w) / 2.0 + (self.pan_x_var.get() * (px_w - fit_w) / 2.0))
                pos_y = int((px_h - fit_h) / 2.0 + (self.pan_y_var.get() * (px_h - fit_h) / 2.0))
                map_img.paste(resized, (pos_x, pos_y))

            selected_paper = self.paper_size_var.get()
            if selected_paper == "Custom":
                w_pts = self.custom_w_var.get() * inch
                h_pts = self.custom_h_var.get() * inch
                base_pagesize = (w_pts, h_pts)
            else:
                page_size_map = {
                    "Letter": letter,
                    "Legal": legal,
                    "A3": A3,
                    "A4": A4,
                    "A5": A5
                }
                base_pagesize = page_size_map.get(selected_paper, letter)

            pagesize = landscape(base_pagesize) if orient_str == "landscape" else base_pagesize
            c = canvas.Canvas(out_path, pagesize=pagesize)
            c.setTitle(f"{base_name} Assembled Map ({dpi} DPI)")

            processed = 0
            for r in range(rows):
                for col in range(cols):
                    processed += 1
                    
                    self.after(0, lambda p=processed, r_num=r+1, c_num=col+1: (
                        pbar.config(value=p),
                        lbl_status.config(text=f"Rendering Tile [{c_num},{r_num}] at {dpi} DPI..."),
                        lbl_count.config(text=f"{p} / {total_tiles} pages rendered")
                    ))

                    x0 = round(col * dims["step_w"] * dpi)
                    y0 = round(r * dims["step_h"] * dpi)
                    crop_w_px = round(dims["print_w"] * dpi)
                    crop_h_px = round(dims["print_h"] * dpi)

                    tile = map_img.crop((x0, y0, x0 + crop_w_px, y0 + crop_h_px))
                    tmp = os.path.join(OUTPUT_DIR, f"_temp_tile_{r}_{col}.png")
                    tile.save(tmp, format="PNG")

                    tile_x = margin * inch
                    tile_y = margin * inch
                    tile_w = dims["print_w"] * inch
                    tile_h = dims["print_h"] * inch

                    # Pass interpolate parameter to enable or disable smoothing in PDF viewers
                    smooth_pdf = not self.disable_smoothing_var.get()
                    
                    # 2. Draw directly without the unsupported 'interpolate' keyword
                    c.drawImage(
                        ImageReader(tmp), 
                        tile_x, 
                        tile_y, 
                        width=tile_w, 
                        height=tile_h, 
                        preserveAspectRatio=False
                    )

                    if self.show_guides_var.get():
                        c.setLineWidth(0.4)
                        c.setStrokeColor(colors.gray)

                        corners = [(tile_x, tile_y), (tile_x + tile_w, tile_y), (tile_x, tile_y + tile_h), (tile_x + tile_w, tile_y + tile_h)]
                        for cx, cy in corners:
                            c.line(cx - 0.1 * inch, cy, cx + 0.1 * inch, cy)
                            c.line(cx, cy - 0.1 * inch, cx, cy + 0.1 * inch)

                    if self.show_headers_var.get():
                        c.setFont("Helvetica", 7)
                        c.setFillColor(colors.gray)
                        c.drawString(margin * inch, (page_h - margin + 0.05) * inch, f"{base_name} — Tile [{col+1},{r+1}] (Row {r+1}, Col {col+1} of {rows}x{cols})")

                    c.showPage()
                    if os.path.exists(tmp):
                        os.remove(tmp)

            c.save()

            self.after(0, lambda: (
                progress_win.destroy(),
                messagebox.showinfo("Success", f"PDF Generated Successfully!\n\nSaved to output folder:\n{out_path}")
            ))

        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    try:
        app = MapTilerApp()
        app.mainloop()
    except Exception as e:
        import traceback
        print("\n" + "="*50)
        print("AN ERROR OCCURRED ON LAUNCH:")
        print("="*50 + "\n")
        traceback.print_exc()
        print("\n" + "="*50)
        input("Press ENTER to exit...")
