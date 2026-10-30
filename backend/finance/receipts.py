"""Receipt payload and PDF rendering."""

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A5
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


def receipt_data(payment):
    student = payment.student
    return {
        "receipt_number": payment.receipt_number,
        "status": payment.status,
        "school": payment.tenant.name,
        "currency": "GHS",
        "date": payment.payment_date.isoformat(),
        "student": {
            "name": f"{student.first_name} {student.last_name}",
            "admission_number": student.admission_number,
            "grade_level": student.grade_level,
        },
        "payer_name": payment.payer_name,
        "method": payment.method.name,
        "reference": payment.reference,
        "amount": str(payment.amount),
        "allocations": [
            {"invoice": a.invoice.number, "amount": str(a.amount)}
            for a in payment.allocations.select_related("invoice").order_by("created_at")
        ],
        "advance_credit": str(payment.unallocated),
        "refunded": str(payment.refunded_total),
    }


def render_receipt_pdf(payment):
    data = receipt_data(payment)
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A5)
    width, height = A5
    y = height - 18 * mm
    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(15 * mm, y, data["school"][:48])
    y -= 7 * mm
    pdf.setFont("Helvetica", 10)
    pdf.drawString(15 * mm, y, "OFFICIAL RECEIPT" + ("  (REVERSED)" if payment.status == "reversed" else ""))
    y -= 9 * mm

    def row(label, value):
        nonlocal y
        pdf.setFont("Helvetica", 9)
        pdf.setFillColor(colors.grey)
        pdf.drawString(15 * mm, y, label)
        pdf.setFillColor(colors.black)
        pdf.drawString(55 * mm, y, str(value)[:60])
        y -= 6 * mm

    row("Receipt no.", data["receipt_number"])
    row("Date", data["date"])
    row("Student", data["student"]["name"])
    row("Admission no.", data["student"]["admission_number"])
    row("Class", data["student"]["grade_level"])
    if data["payer_name"]:
        row("Received from", data["payer_name"])
    row("Method", data["method"])
    if data["reference"]:
        row("Reference", data["reference"])
    for allocation in data["allocations"]:
        row("Applied to", f"{allocation['invoice']}  GHS {allocation['amount']}")
    if payment.unallocated:
        row("Advance credit", f"GHS {data['advance_credit']}")
    y -= 3 * mm
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(15 * mm, y, f"Amount received: GHS {data['amount']}")
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
