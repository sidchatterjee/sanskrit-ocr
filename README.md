# Sanskrit OCR & Transliteration

OCR Sanskrit PDFs using the Google Cloud Vision API and transliterate the output to Roman script.

## Features

- Upload a PDF and select a page range
- Specify source script: Devanagari, Bengali, or Malayalam
- Transliterate to IAST, Kolkata, ITRANS, Harvard-Kyoto, or SLP
- Two frontends: Flask web app and tkinter desktop app

## Requirements

- Python 3.9+ for the web app (`app.py`)
- Python 3.12+ for the desktop app (`desktop.py`) — needs a Tcl/Tk that supports your macOS version
- A Google Cloud API key with the [Cloud Vision API](https://console.cloud.google.com/apis/library/vision.googleapis.com) enabled

## Setup

```bash
pip install -r requirements.txt
```

## Usage

### Web app

```bash
python3 app.py
```

Open http://127.0.0.1:5000 in your browser.

### Desktop app

```bash
python3.14 desktop.py
```

A native window will open.

## How it works

1. [PyMuPDF](https://pymupdf.readthedocs.io/) renders each selected PDF page to a 300 DPI image
2. Each image is sent to the Google Cloud Vision API (`DOCUMENT_TEXT_DETECTION`) with a BCP 47 language hint (e.g. `sa-Deva`)
3. The OCR text is transliterated from the source script to the chosen Roman scheme using [indic-transliteration](https://github.com/indic-transliteration/indic_transliteration_py)
4. The result is saved as a `.txt` file
