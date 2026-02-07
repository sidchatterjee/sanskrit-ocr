import base64
import io
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import fitz  # PyMuPDF
import requests as http_requests
from indic_transliteration import sanscript
from indic_transliteration.sanscript import transliterate

# ── Mappings ──────────────────────────────────────────────────────────

SCRIPT_INPUT = {
    "Devanagari (Deva)": ("Deva", sanscript.DEVANAGARI),
    "Bengali (Beng)": ("Beng", sanscript.BENGALI),
    "Malayalam (Mlym)": ("Mlym", sanscript.MALAYALAM),
}

SCRIPT_OUTPUT = {
    "IAST": sanscript.IAST,
    "Kolkata": sanscript.KOLKATA,
    "ITRANS": sanscript.ITRANS,
    "Harvard-Kyoto": sanscript.HK,
    "SLP": sanscript.SLP1,
}

VISION_API_URL = "https://vision.googleapis.com/v1/images:annotate"

# ── Backend logic ─────────────────────────────────────────────────────


def ocr_page_image(image_bytes, api_key, language_hint):
    """Send a single page image to Google Cloud Vision API for OCR."""
    payload = {
        "requests": [
            {
                "image": {"content": base64.b64encode(image_bytes).decode("utf-8")},
                "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                "imageContext": {"languageHints": [language_hint]},
            }
        ]
    }
    resp = http_requests.post(
        VISION_API_URL,
        params={"key": api_key},
        json=payload,
        timeout=120,
    )
    if resp.status_code == 400:
        body = resp.json()
        msg = body.get("error", {}).get("message", "")
        if "API key" in msg or "invalid" in msg.lower():
            raise ValueError(
                "The API key you entered appears to be invalid. "
                "Please double-check it and try again."
            )
        raise ValueError(
            f"Google Cloud rejected the request: {msg or 'unknown error'}. "
            "Please verify your API key has the Cloud Vision API enabled."
        )
    if resp.status_code == 403:
        raise PermissionError(
            "Access denied. Your API key may not have permission to use the "
            "Cloud Vision API, or billing may not be enabled on your "
            "Google Cloud project. Please check your Google Cloud console."
        )
    if resp.status_code == 429:
        raise RuntimeError(
            "Too many requests \u2014 Google Cloud rate-limited your API key. "
            "Please wait a minute and try again, or check your quota in "
            "the Google Cloud console."
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Unexpected response from Google Cloud (HTTP {resp.status_code}). "
            "Please try again later. If the problem persists, check the "
            "Google Cloud status dashboard."
        )
    result = resp.json()
    responses = result.get("responses", [])
    if not responses:
        return ""
    first = responses[0]
    if "error" in first:
        err_msg = first["error"].get("message", "Unknown error")
        raise RuntimeError(f"Google Cloud returned an error for a page: {err_msg}")
    annotation = first.get("fullTextAnnotation", {})
    return annotation.get("text", "")


def extract_page_images(pdf_bytes, start_page, end_page):
    """Convert PDF pages to PNG images. Pages are 1-indexed."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total_pages = len(doc)
    if start_page < 1 or end_page < 1:
        doc.close()
        raise ValueError("Page numbers must be 1 or greater.")
    if start_page > total_pages or end_page > total_pages:
        doc.close()
        raise ValueError(
            f"Your PDF has {total_pages} page(s), but you requested "
            f"pages {start_page}\u2013{end_page}. Please adjust the page range."
        )
    if start_page > end_page:
        doc.close()
        raise ValueError("The start page cannot be after the end page.")
    images = []
    for page_num in range(start_page - 1, end_page):
        page = doc[page_num]
        pix = page.get_pixmap(dpi=300)
        images.append(pix.tobytes("png"))
    doc.close()
    return images


# ── GUI ───────────────────────────────────────────────────────────────


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Sanskrit OCR & Transliteration")
        self.resizable(False, False)

        self._pdf_path = tk.StringVar()
        self._start_page = tk.StringVar(value="1")
        self._end_page = tk.StringVar(value="1")
        self._script = tk.StringVar(value=list(SCRIPT_INPUT.keys())[0])
        self._output_scheme = tk.StringVar(value=list(SCRIPT_OUTPUT.keys())[0])
        self._api_key = tk.StringVar()

        self._build_ui()

    # ── layout ────────────────────────────────────────────────────

    def _build_ui(self):
        pad = {"padx": 10, "pady": 4}
        frame = ttk.Frame(self, padding=16)
        frame.grid(sticky="nsew")

        row = 0

        # PDF file
        ttk.Label(frame, text="PDF File").grid(
            row=row, column=0, sticky="w", **pad
        )
        row += 1
        file_frame = ttk.Frame(frame)
        file_frame.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)
        self._file_entry = ttk.Entry(
            file_frame, textvariable=self._pdf_path, width=48
        )
        self._file_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(file_frame, text="Browse\u2026", command=self._browse_pdf).pack(
            side="left", padx=(6, 0)
        )
        row += 1

        # Page range
        ttk.Label(frame, text="Start Page").grid(
            row=row, column=0, sticky="w", **pad
        )
        ttk.Label(frame, text="End Page").grid(
            row=row, column=1, sticky="w", **pad
        )
        row += 1
        ttk.Entry(frame, textvariable=self._start_page, width=10).grid(
            row=row, column=0, sticky="w", **pad
        )
        ttk.Entry(frame, textvariable=self._end_page, width=10).grid(
            row=row, column=1, sticky="w", **pad
        )
        row += 1

        # Language (fixed) & Script
        ttk.Label(frame, text="Language").grid(
            row=row, column=0, sticky="w", **pad
        )
        ttk.Label(frame, text="Script").grid(
            row=row, column=1, sticky="w", **pad
        )
        row += 1
        lang_combo = ttk.Combobox(
            frame, values=["Sanskrit (sa)"], state="readonly", width=16
        )
        lang_combo.current(0)
        lang_combo.grid(row=row, column=0, sticky="w", **pad)
        ttk.Combobox(
            frame,
            textvariable=self._script,
            values=list(SCRIPT_INPUT.keys()),
            state="readonly",
            width=20,
        ).grid(row=row, column=1, sticky="w", **pad)
        row += 1

        # Output scheme
        ttk.Label(frame, text="Output Transliteration").grid(
            row=row, column=0, sticky="w", columnspan=2, **pad
        )
        row += 1
        ttk.Combobox(
            frame,
            textvariable=self._output_scheme,
            values=list(SCRIPT_OUTPUT.keys()),
            state="readonly",
            width=20,
        ).grid(row=row, column=0, sticky="w", **pad)
        row += 1

        # API key
        ttk.Label(frame, text="Google Cloud API Key").grid(
            row=row, column=0, sticky="w", columnspan=2, **pad
        )
        row += 1
        ttk.Entry(frame, textvariable=self._api_key, show="*", width=48).grid(
            row=row, column=0, columnspan=3, sticky="ew", **pad
        )
        ttk.Label(
            frame,
            text="Your key is sent directly to Google and is not stored.",
            foreground="gray",
            font=("TkDefaultFont", 10),
        ).grid(row=row + 1, column=0, columnspan=3, sticky="w", padx=10)
        row += 2

        # Run button
        self._run_btn = ttk.Button(
            frame, text="Run OCR & Transliterate", command=self._on_run
        )
        self._run_btn.grid(
            row=row, column=0, columnspan=3, sticky="ew", padx=10, pady=(12, 4)
        )
        row += 1

        # Status label
        self._status_var = tk.StringVar()
        self._status_label = ttk.Label(
            frame, textvariable=self._status_var, wraplength=420
        )
        self._status_label.grid(
            row=row, column=0, columnspan=3, sticky="w", **pad
        )

    # ── callbacks ─────────────────────────────────────────────────

    def _browse_pdf(self):
        path = filedialog.askopenfilename(
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")]
        )
        if path:
            self._pdf_path.set(path)

    def _set_busy(self, busy):
        state = "disabled" if busy else "normal"
        self._run_btn.configure(state=state)
        if busy:
            self._status_var.set("Processing \u2014 this may take a moment\u2026")
            self._status_label.configure(foreground="black")

    def _on_run(self):
        # ── validate ──────────────────────────────────────────────
        pdf_path = self._pdf_path.get().strip()
        if not pdf_path:
            messagebox.showwarning("Missing file", "Please select a PDF file.")
            return
        if not os.path.isfile(pdf_path):
            messagebox.showerror("File not found", f"Cannot find:\n{pdf_path}")
            return

        try:
            start_page = int(self._start_page.get())
            end_page = int(self._end_page.get())
        except ValueError:
            messagebox.showwarning(
                "Invalid pages",
                "Please enter valid whole numbers for the page range.",
            )
            return

        api_key = self._api_key.get().strip()
        if not api_key:
            messagebox.showwarning(
                "Missing API key", "Please enter your Google Cloud API key."
            )
            return

        script_label = self._script.get()
        script_subtag, input_scheme = SCRIPT_INPUT[script_label]
        output_name = self._output_scheme.get()
        output_scheme = SCRIPT_OUTPUT[output_name]
        language_hint = f"sa-{script_subtag}"

        self._set_busy(True)

        threading.Thread(
            target=self._process,
            args=(
                pdf_path,
                start_page,
                end_page,
                api_key,
                language_hint,
                input_scheme,
                output_scheme,
                output_name,
            ),
            daemon=True,
        ).start()

    def _process(
        self,
        pdf_path,
        start_page,
        end_page,
        api_key,
        language_hint,
        input_scheme,
        output_scheme,
        output_name,
    ):
        try:
            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()

            # 1. Extract page images
            try:
                images = extract_page_images(pdf_bytes, start_page, end_page)
            except ValueError as exc:
                self._finish_error(str(exc))
                return
            except Exception:
                self._finish_error(
                    "Something went wrong while reading the PDF. "
                    "Please make sure the file is not corrupted or "
                    "password-protected."
                )
                return

            # 2. OCR each page
            all_text_parts = []
            total = len(images)
            for idx, img_bytes in enumerate(images, start=start_page):
                self._set_status(
                    f"Processing page {idx} "
                    f"({idx - start_page + 1} of {total})\u2026"
                )
                try:
                    page_text = ocr_page_image(img_bytes, api_key, language_hint)
                except (ValueError, PermissionError, RuntimeError) as exc:
                    self._finish_error(str(exc))
                    return
                except http_requests.exceptions.Timeout:
                    self._finish_error(
                        f"The request timed out while processing page {idx}. "
                        "This can happen with very large or complex pages. "
                        "Try a smaller page range or try again later."
                    )
                    return
                except http_requests.exceptions.ConnectionError:
                    self._finish_error(
                        "Could not connect to Google Cloud. "
                        "Please check your internet connection and try again."
                    )
                    return
                if page_text:
                    all_text_parts.append(f"--- Page {idx} ---\n{page_text}")

            if not all_text_parts:
                self._finish_error(
                    "The OCR process did not detect any text on the "
                    "selected pages. This could mean the pages are blank, "
                    "the images are too low-quality, or the wrong script "
                    "was selected."
                )
                return

            combined_ocr_text = "\n\n".join(all_text_parts)

            # 3. Transliterate
            self._set_status("Transliterating\u2026")
            try:
                transliterated = transliterate(
                    combined_ocr_text, input_scheme, output_scheme
                )
            except Exception:
                self._finish_error(
                    "An error occurred during transliteration. "
                    "Please make sure you selected the correct source "
                    "script for your document."
                )
                return

            # 4. Save file
            base = os.path.splitext(os.path.basename(pdf_path))[0]
            default_name = f"{base}_p{start_page}-{end_page}_{output_name}.txt"
            self.after(0, self._save_result, transliterated, default_name)

        except Exception as exc:
            self._finish_error(f"An unexpected error occurred: {exc}")

    def _save_result(self, text, default_name):
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            initialfile=default_name,
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self._status_var.set(f"Saved to {path}")
            self._status_label.configure(foreground="green")
        else:
            self._status_var.set("Save cancelled.")
            self._status_label.configure(foreground="gray")
        self._run_btn.configure(state="normal")

    # ── thread-safe helpers ───────────────────────────────────────

    def _set_status(self, msg):
        self.after(0, self._status_var.set, msg)

    def _finish_error(self, msg):
        def _show():
            self._status_var.set("")
            self._run_btn.configure(state="normal")
            messagebox.showerror("Error", msg)

        self.after(0, _show)


if __name__ == "__main__":
    App().mainloop()
