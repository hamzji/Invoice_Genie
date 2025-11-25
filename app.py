import re
import pdfplumber
from flask import Flask, render_template, request

app = Flask(__name__)

def detect_vendor(text):
    if "CooperVision" in text:
        return "CooperVision"
    if "Paragon" in text:
        return "Paragon"
    return "Unknown Vendor"

def extract_currency(text):
    if "$" in text:
        return "USD"
    return "Unknown"

def parse_invoice(file):
    with pdfplumber.open(file) as pdf:
        full_text = "\n".join([page.extract_text() for page in pdf.pages])

    vendor = detect_vendor(full_text)
    currency = extract_currency(full_text)

    rows = []
    for page in pdf.pages:
        table = page.extract_table()
        if not table:
            continue
        for row in table:
            if len(row) < 5:
                continue
            item_col = str(row[1]).upper()  # e.g. CRT100, CRTDA
            total_col = row[-1]

            if "CRT" in item_col:
                qty = row[0]
                try:
                    qty = float(qty)
                except:
                    continue

                try:
                    price = float(str(total_col).replace(",", ""))
                except:
                    price = 0.0

                rows.append((item_col, qty, price))

    summary = {"CRT 100": {"qty": 0, "total": 0}, "CRT 100 DA": {"qty": 0, "total": 0}}

    for item, qty, total in rows:
        if "DA" in item:
            summary["CRT 100 DA"]["qty"] += 1
            summary["CRT 100 DA"]["total"] += total
        else:
            summary["CRT 100"]["qty"] += 1
            summary["CRT 100"]["total"] += total

    return vendor, currency, summary

@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    filename = None

    # POST 요청: 파일 업로드
    if request.method == "POST":
        file = request.files.get("file")
        if not file:
            return render_template("index.html", result=None, filename=None)

        filename = file.filename
        vendor, currency, summary = parse_invoice(file)

        # 파싱 결과 묶기
        result = {
            "vendor": vendor,
            "currency": currency,
            "summary": summary
        }

    # GET 또는 결과 반환
    return render_template("index.html", result=result, filename=filename)

        filename = file.filename
        vendor, currency, summary = parse_invoice(file)

        result = {"vendor": vendor, "currency": currency, "summary": summary}

    return render_template("index.html", result=result, filename=filename)


if __name__ == "__main__":
    app.run(debug=True)
