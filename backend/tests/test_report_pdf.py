from app.services.report_pdf import render_report_pdf


def test_pdf_escapes_markup_and_wraps_large_records():
    payload = {'filters': {'device_id': '<b>literal & data</b>'}, 'anomalies': [{'outcome': 'Very long explanation ' * 300, 'affected_cell': None} for _ in range(25)]}
    pdf = render_report_pdf(payload, 17)
    assert pdf.startswith(b'%PDF-')
    assert len(pdf) > 5000
