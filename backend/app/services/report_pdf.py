"""Paginated PDF rendering of the immutable report snapshot.

No live reads or prediction calls: exports remain reproducible after retention.
All enabled sections and nested values are rendered, without row truncation.
"""
from __future__ import annotations

import io
from pathlib import Path
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer


def render_report_pdf(payload: dict, report_id: int) -> bytes:
    fonts = Path(__file__).resolve().parents[1] / 'assets' / 'fonts'
    if 'BMSUnicode' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('BMSUnicode', str(fonts / 'DejaVuSans.ttf')))
        pdfmetrics.registerFont(TTFont('BMSUnicode-Bold', str(fonts / 'DejaVuSans-Bold.ttf')))
        pdfmetrics.registerFontFamily('BMSUnicode', normal='BMSUnicode', bold='BMSUnicode-Bold')
    buffer = io.BytesIO()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle('Value', fontName='BMSUnicode', fontSize=9, leading=13, spaceAfter=5, textColor=colors.HexColor('#251f21'), alignment=TA_LEFT, splitLongWords=True))
    for name in ('Title', 'Heading2', 'Heading3'):
        styles[name].fontName = 'BMSUnicode-Bold'
    story = [Paragraph(f'Battery monitoring report #{report_id}', styles['Title']), Paragraph('Stored snapshot export. Times retain the snapshot timezone. Possible causes are not confirmed physical failures.', styles['Value']), Spacer(1, 12)]

    def text(value):
        return escape(str(value)).replace('\n', '<br/>')

    def walk(value, depth=0):
        if isinstance(value, dict):
            if not value:
                story.append(Paragraph('No data', styles['Value']))
            for key, child in value.items():
                label = text(key.replace('_', ' ').capitalize())
                if isinstance(child, (dict, list)):
                    story.append(Paragraph(label, styles['Heading2' if depth < 1 else 'Heading3']))
                    walk(child, depth + 1)
                else:
                    story.append(Paragraph(f'<b>{label}:</b> {text(child) if child is not None else "Not available"}', styles['Value']))
        elif isinstance(value, list):
            if not value:
                story.append(Paragraph('No records', styles['Value']))
            for i, child in enumerate(value, 1):
                if isinstance(child, (list, tuple)) and len(child) == 2 and not isinstance(child[0], (dict, list, tuple)):
                    story.append(Paragraph(f'<b>{text(child[0])}:</b> {text(child[1])}', styles['Value']))
                    continue
                if not isinstance(child, (list, dict)):
                    story.append(Paragraph(text(child), styles['Value']))
                    continue
                story.append(Paragraph(f'Record {i}', styles['Heading3']))
                walk(child, depth + 1)
        else:
            story.append(Paragraph(text(value), styles['Value']))
    order = ["summary", "period", "device", "data_availability", "telemetry", "soh", "anomalies", "actions", "filters", "model_metadata", "included_sections", "report_type", "generated_at", "format_version"]
    ordered = {k: payload[k] for k in order if k in payload}
    ordered.update({k: v for k, v in payload.items() if k not in ordered})
    walk(ordered)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        canvas.drawString(40, 24, f'BMS Monitor | report #{report_id} | stored snapshot')
        canvas.drawRightString(A4[0] - 40, 24, f'Page {doc.page}')
        canvas.restoreState()
    SimpleDocTemplate(buffer, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=38, bottomMargin=42, title=f'BMS report {report_id}', author='BMS Monitor').build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
