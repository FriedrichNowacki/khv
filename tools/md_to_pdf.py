#!/usr/bin/env python3
"""
Universeller Markdown zu PDF Konverter unter Windows
Nutzt das installierte Google Chrome (Headless) für pixelgenauen PDF-Druck
inklusive schönem Styling für Tabellen, Codeblöcke und Alert-Boxen.
"""

import sys
import os
import subprocess
import tempfile
import markdown

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
EDGE_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

CSS_STYLE = """
<style>
    body {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
        font-size: 14px;
        line-height: 1.6;
        color: #24292e;
        margin: 40px;
        max-width: 900px;
    }
    h1, h2, h3, h4, h5, h6 {
        margin-top: 24px;
        margin-bottom: 16px;
        font-weight: 600;
        line-height: 1.25;
        border-bottom: 1px solid #eaecef;
        padding-bottom: 0.3em;
    }
    h1 { font-size: 2em; }
    h2 { font-size: 1.5em; }
    h3 { font-size: 1.25em; }
    table {
        border-collapse: collapse;
        width: 100%;
        margin: 16px 0;
        page-break-inside: avoid;
    }
    th, td {
        border: 1px solid #dfe2e5;
        padding: 6px 13px;
    }
    th {
        background-color: #f6f8fa;
        font-weight: 600;
    }
    tr:nth-child(2n) {
        background-color: #f6f8fa;
    }
    pre, code {
        font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, Courier, monospace;
        font-size: 12px;
    }
    pre {
        background-color: #f6f8fa;
        border-radius: 6px;
        padding: 16px;
        overflow: auto;
        border: 1px solid #e1e4e8;
    }
    blockquote {
        margin: 0;
        padding: 0 1em;
        color: #6a737d;
        border-left: 0.25em solid #dfe2e5;
        background-color: #f8f9fa;
        border-radius: 3px;
    }
    img {
        max-width: 100%;
        height: auto;
    }
    hr {
        height: 0.25em;
        padding: 0;
        margin: 24px 0;
        background-color: #e1e4e8;
        border: 0;
    }
</style>
"""

def get_browser_exe():
    if os.path.exists(CHROME_PATH):
        return CHROME_PATH
    if os.path.exists(EDGE_PATH):
        return EDGE_PATH
    raise FileNotFoundError("Weder Chrome noch Edge unter den Standardpfaden gefunden!")

def convert_md_to_pdf(md_path, pdf_path=None):
    if not os.path.exists(md_path):
        print(f"[FEHLER] Markdown-Datei existiert nicht: {md_path}")
        return False

    if not pdf_path:
        pdf_path = os.path.splitext(md_path)[0] + ".pdf"

    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    # Markdown in HTML konvertieren (mit Tables, Codehilite, Fenced Code)
    html_body = markdown.markdown(
        md_text,
        extensions=['tables', 'fenced_code', 'nl2br']
    )

    full_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{os.path.basename(md_path)}</title>
    {CSS_STYLE}
</head>
<body>
    {html_body}
</body>
</html>"""

    # Temporäre HTML-Datei speichern
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".html", delete=False) as tf:
        tf.write(full_html)
        temp_html_path = tf.name

    try:
        browser_exe = get_browser_exe()
        print(f"[INFO] Rendere PDF via {os.path.basename(browser_exe)}: {os.path.basename(md_path)} -> {os.path.basename(pdf_path)}")

        cmd = [
            browser_exe,
            "--headless=new",
            "--disable-gpu",
            f"--print-to-pdf={os.path.abspath(pdf_path)}",
            f"file:///{os.path.abspath(temp_html_path).replace(os.sep, '/')}"
        ]

        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(f"[ERFOLG] PDF erfolgreich erstellt: {os.path.abspath(pdf_path)}")
        return True
    finally:
        if os.path.exists(temp_html_path):
            os.remove(temp_html_path)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = sys.argv[1]
    else:
        # Standardmäßig README.md
        target = "README.md"

    convert_md_to_pdf(target)
