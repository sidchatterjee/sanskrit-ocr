import base64
import io
import os

import fitz  # PyMuPDF
import requests as http_requests
from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    send_file,
)
from indic_transliteration import sanscript
from indic_transliteration.sanscript import transliterate

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Source script mapping (BCP 47 script subtag -> indic_transliteration scheme)
SCRIPT_INPUT = {
    "Deva": sanscript.DEVANAGARI,
    "Beng": sanscript.BENGALI,
    "Mlym": sanscript.MALAYALAM,
}

# Target transliteration schemes
SCRIPT_OUTPUT = {
    "IAST": sanscript.IAST,
    "Kolkata": sanscript.KOLKATA,
    "ITRANS": sanscript.ITRANS,
    "Harvard-Kyoto": sanscript.HK,
    "SLP": sanscript.SLP1,
}

VISION_API_URL = "https://vision.googleapis.com/v1/images:annotate"


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
            "Cloud Vision API, or billing may not be enabled on your Google Cloud project. "
            "Please check your Google Cloud console."
        )
    if resp.status_code == 429:
        raise RuntimeError(
            "Too many requests — Google Cloud rate-limited your API key. "
            "Please wait a minute and try again, or check your quota in the "
            "Google Cloud console."
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
            f"pages {start_page}–{end_page}. Please adjust the page range."
        )
    if start_page > end_page:
        doc.close()
        raise ValueError("The start page cannot be after the end page.")
    images = []
    for page_num in range(start_page - 1, end_page):  # 0-indexed internally
        page = doc[page_num]
        pix = page.get_pixmap(dpi=300)
        images.append(pix.tobytes("png"))
    doc.close()
    return images


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        return render_template("index.html")

    def err(msg, code=400):
        return jsonify({"error": msg}), code

    # ── Collect and validate form inputs ──────────────────────────────
    pdf_file = request.files.get("pdf")
    if not pdf_file or pdf_file.filename == "":
        return err("Please select a PDF file to upload.")
    if not pdf_file.filename.lower().endswith(".pdf"):
        return err("The uploaded file does not appear to be a PDF.")

    try:
        start_page = int(request.form.get("start_page", ""))
        end_page = int(request.form.get("end_page", ""))
    except (ValueError, TypeError):
        return err("Please enter valid whole numbers for the page range.")

    script_subtag = request.form.get("script")
    if script_subtag not in SCRIPT_INPUT:
        return err("Please select a valid script.")

    output_scheme_name = request.form.get("output_scheme")
    if output_scheme_name not in SCRIPT_OUTPUT:
        return err("Please select a valid output transliteration scheme.")

    api_key = request.form.get("api_key", "").strip()
    if not api_key:
        return err("Please enter your Google Cloud API key.")

    # ── Process ───────────────────────────────────────────────────────
    pdf_bytes = pdf_file.read()

    # 1. Extract page images
    try:
        images = extract_page_images(pdf_bytes, start_page, end_page)
    except ValueError as exc:
        return err(str(exc))
    except Exception:
        return err(
            "Something went wrong while reading the PDF. "
            "Please make sure the file is not corrupted or password-protected."
        )

    # 2. OCR each page
    language_hint = f"sa-{script_subtag}"
    all_text_parts = []
    for i, img_bytes in enumerate(images, start=start_page):
        try:
            page_text = ocr_page_image(img_bytes, api_key, language_hint)
        except (ValueError, PermissionError, RuntimeError) as exc:
            return err(str(exc))
        except http_requests.exceptions.Timeout:
            return err(
                f"The request timed out while processing page {i}. "
                "This can happen with very large or complex pages. "
                "Try a smaller page range or try again later."
            )
        except http_requests.exceptions.ConnectionError:
            return err(
                "Could not connect to Google Cloud. "
                "Please check your internet connection and try again."
            )
        if page_text:
            all_text_parts.append(f"--- Page {i} ---\n{page_text}")

    if not all_text_parts:
        return err(
            "The OCR process did not detect any text on the selected pages. "
            "This could mean the pages are blank, the images are too low-quality, "
            "or the wrong script was selected."
        )

    combined_ocr_text = "\n\n".join(all_text_parts)

    # 3. Transliterate
    try:
        transliterated = transliterate(
            combined_ocr_text,
            SCRIPT_INPUT[script_subtag],
            SCRIPT_OUTPUT[output_scheme_name],
        )
    except Exception:
        return err(
            "An error occurred during transliteration. "
            "Please make sure you selected the correct source script for your document."
        )

    # 4. Return as downloadable file
    buf = io.BytesIO(transliterated.encode("utf-8"))
    buf.seek(0)
    filename = (
        f"{os.path.splitext(pdf_file.filename)[0]}"
        f"_p{start_page}-{end_page}"
        f"_{output_scheme_name}.txt"
    )
    return send_file(
        buf,
        as_attachment=True,
        download_name=filename,
        mimetype="text/plain; charset=utf-8",
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
