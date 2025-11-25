import os
import re
from collections import defaultdict
from datetime import datetime

from PyPDF2 import PdfReader
from flask import Flask, request, render_template_string

app = Flask(__name__)

# -------------------------------------------------
# 📌 PDF 텍스트 추출
# -------------------------------------------------
def extract_text(pdf_path: str) -> str:
    """PDF에서 텍스트만 가볍게 추출 (PyPDF2 사용)."""
    reader = PdfReader(pdf_path)
    texts = []
    for page in reader.pages:
        txt = page.extract_text() or ""
        texts.append(txt)
    return "\n".join(texts)

# -------------------------------------------------
# 🗓 Invoice Date 추출
#   예: "Invoice Date 11/19/2025" or "Invoice Date: 11-19-2025"
# -------------------------------------------------
def extract_invoice_date(text: str):
    m = re.search(
        r"Invoice\s+Date[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{4})",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None, None  # raw, korean

    raw = m.group(1).strip()

    dt = None
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            dt = datetime.strptime(raw, fmt)
            break
        except ValueError:
            continue

    if not dt:
        # 날짜 파싱 실패하면 원문만 반환
        return raw, None

    kr = f"{dt.year}년 {dt.month}월 {dt.day}일"
    return raw, kr


# -------------------------------------------------
# 🧾 PO 번호 추출 (중복 제거)
#   예: PO2511000059, PO2509000012 ...
# -------------------------------------------------
def extract_po_numbers(text: str):
    po_list = re.findall(r"PO\d{6,20}", text)
    # 중복 제거 + 정렬
    return sorted(set(po_list))


# -------------------------------------------------
# 🧙 인보이스 종류 판별
# -------------------------------------------------
def detect_vendor(text: str) -> str:
    upper = text.upper()
    if "CRT" in upper or "PARAGON" in upper:
        return "PARAGON"
    if "PHYSIOL" in upper or "PODEYE" in upper:
        return "PHYSIOL"
    return "UNKNOWN"


# -------------------------------------------------
# 🔮 Paragon 인보이스 파싱
#   - 라인별 수량/금액 추출
#   - CRT 100 / CRT 100 DA 단위로 합산
# -------------------------------------------------
def parse_paragon(text: str):
    """
    Paragon 인보이스에서 CRT 100 / CRT 100 DA 품목 수량 + 금액을 유연하게 추출
    """
    grouped = defaultdict(lambda: {"qty": 0.0, "amount": 0.0})

    # 품목이 있는 줄만 추출
    for line in text.splitlines():
        upper = line.upper()

        # CRT 품목 라인만 필터링
        if ("CRT" not in upper) or ("100" not in upper):
            continue

        # 수량(맨 앞 숫자) 찾기
        qty_match = re.match(r"\s*(\d+(?:\.\d+)?)", line)
        if not qty_match:
            continue
        qty = float(qty_match.group())

        # 금액(줄에 있는 가장 마지막 금액) 찾기
        numbers = re.findall(r"[\d,]+\.\d+", line)
        if not numbers:
            continue
        amount = float(numbers[-1].replace(",", ""))

        # 품목 분류
        if "DA" in upper:
            key = "CRT 100 DA"
        else:
            key = "CRT 100"

        grouped[key]["qty"] += qty
        grouped[key]["amount"] += amount

    # 결과 정리
    rows = []
    for key in ["CRT 100", "CRT 100 DA"]:
        if key in grouped:
            data = grouped[key]
            # 수량: 소수점 제거
            q = data["qty"]
            qty_display = int(q) if float(q).is_integer() else q
            rows.append({
                "item": key,
                "qty": qty_display,
                "amount": data["amount"],
            })

    total_qty = sum(r["qty"] for r in rows)
    total_amount = sum(r["amount"] for r in rows)

    return {
        "vendor": "Paragon",
        "rows": rows,
        "total_qty": total_qty,
        "total_amount": total_amount,
    }


# -------------------------------------------------
# 💜 HTML 템플릿
# -------------------------------------------------
HTML = """
<!DOCTYPE html>
<html>
<head>
<title>Invoice Genie 🧙‍♂️</title>
<style>
body { font-family:-apple-system, sans-serif; background:#f7ecff; padding:40px; color:#4b0082;}
h1 { font-size:42px; font-weight:800; }
.drop { border:3px dashed #a96df0; padding:60px; text-align:center; font-size:20px; border-radius:25px; cursor:pointer; background:white; transition:background 0.2s;}
.drop.dragover {background:#f2e6ff;}
button { padding:12px 40px; font-size:18px; border-radius:15px; background:#884dff; color:white; border:none; cursor:pointer;}
button:hover { background:#6e33ff; }
.result { margin-top:30px; padding:25px; border-radius:15px; background:white; border:1px solid #ddd; font-size:18px;}
table { width:100%; border-collapse:collapse; font-size:16px; margin-top:15px;}
th,td { padding:8px; border:1px solid #ccc; text-align:center;}
.filename { font-size:16px; color:#333; margin-top:10px;}
.filename b { color:#4b0082; }
.err { background:#ffdede; color:#b30000; border:2px solid #ff8a8a; }
</style>
</head>

<body>
<h1>🧙‍♂️ Invoice Genie</h1>
<div class="subtitle">
    인보이스 PDF에서 제품 정보, 수량, 금액 등을 자동으로 분석해주는 도구입니다.
</div>

<form method="POST" enctype="multipart/form-data">
    <!-- 서버에서 넘어온 마지막 파일명을 data-filename 으로 넣어둠 -->
    <div class="drop" id="dropZone" data-filename="{{ filename or '' }}">
        <span id="dropText">📎 PDF 파일을 드래그하거나 클릭해 업로드하세요</span>
    </div>
    <input type="file" name="file" id="fileInput" style="display:none" accept="application/pdf">

    <div class="filename" id="fileLabel">
        {% if filename %}
        📤 업로드된 파일: <b>{{ filename }}</b>
        {% else %}
        아직 업로드된 파일이 없습니다.
        {% endif %}
    </div>

    <br><button type="submit">✨ Analyze</button>
</form>

{% if rows is not none %}
<div class="result">
    {% if vendor %}
    <b>📌 Vendor:</b> {{ vendor }}<br>
    {% endif %}

    {% if invoice_date_kr %}
    <b>🗓 Invoice Date:</b> {{ invoice_date_kr }}<br>
    {% elif invoice_date_raw %}
    <b>🗓 Invoice Date:</b> {{ invoice_date_raw }}<br>
    {% endif %}

    {% if po_numbers %}
    <b>📌 Ref PO:</b> {{ ", ".join(po_numbers) }}<br>
    {% endif %}
    <br>

    {% if rows %}
    <table>
        <tr><th>제품명</th><th>총 수량</th><th>총 금액 (USD)</th></tr>
        {% for row in rows %}
        <tr>
            <td>{{ row.item }}</td>
            <td>{{ row.qty }}</td>
            <td>{{ "{:,.2f}".format(row.amount) }}</td>
        </tr>
        {% endfor %}
    </table>

    <br>
    <b>📦 인보이스 전체 총 수량:</b> {{ total_qty }} EA<br>
    <b>💰 인보이스 전체 총 금액:</b> {{ "{:,.2f}".format(total_amount) }} USD
    {% else %}
    <p>CRT 100 / CRT 100 DA 품목을 찾지 못했습니다.</p>
    {% endif %}
</div>
{% elif error %}
<div class="result err"><b>{{ error }}</b></div>
{% endif %}

<script>
const dz  = document.getElementById("dropZone");
const fi  = document.getElementById("fileInput");
const dt  = document.getElementById("dropText");
const flb = document.getElementById("fileLabel");

// ⚡ 페이지 로드 시, 서버에서 넘어온 filename 이 있으면 표시
const initialName = dz.dataset.filename;
if (initialName) {
    dt.textContent  = "✅ " + initialName + " 업로드 완료";
    flb.innerHTML   = "📤 업로드된 파일: <b>" + initialName + "</b>";
}

// 파일 선택/드래그 시 즉시 UI 업데이트
function handleFileSelect(files) {
    if (!files || files.length === 0) return;
    const name = files[0].name;
    dt.textContent = "✅ " + name + " 업로드 준비 완료";
    flb.innerHTML  = "📤 업로드된 파일: <b>" + name + "</b>";
}

dz.addEventListener("click", () => fi.click());
dz.addEventListener("dragover", (e) => {
    e.preventDefault();
    dz.classList.add("dragover");
});
dz.addEventListener("dragleave", () => dz.classList.remove("dragover"));
dz.addEventListener("drop", (e) => {
    e.preventDefault();
    dz.classList.remove("dragover");
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        fi.files = e.dataTransfer.files;
        handleFileSelect(fi.files);
    }
});
fi.addEventListener("change", () => handleFileSelect(fi.files));
</script>
</body>
</html>
"""


# -------------------------------------------------
# 🌍 Flask 라우트
# -------------------------------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    filename = None
    vendor = None
    rows = None
    total_qty = None
    total_amount = None
    invoice_date_raw = None
    invoice_date_kr = None
    po_numbers = None
    error = None

    if request.method == "POST":
        pdf = request.files.get("file")
        if not pdf:
            error = "⚠️ PDF 파일을 업로드해주세요."
        else:
            filename = pdf.filename
            tmp_path = "tmp_invoice.pdf"
            pdf.save(tmp_path)

            # PDF 텍스트 추출 (예외 방지)
            try:
                text = extract_text(tmp_path)
            except Exception:
                error = "❌ PDF 텍스트를 읽는 중 오류가 발생했습니다."
                text = ""

            # 날짜 & PO 번호 추출
            invoice_date_raw, invoice_date_kr = extract_invoice_date(text)
            po_numbers = extract_po_numbers(text)

            # 벤더 판별 + 파싱
            vendor_type = detect_vendor(text)
            parsed = None
            if vendor_type == "PARAGON":
                parsed = parse_paragon(text)
            elif vendor_type == "PHYSIOL":
                parsed = parse_physiol(text)
            else:
                if not error:
                    error = "❌ 지원되지 않는 인보이스 형식입니다."

            # 임시 파일 삭제
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

            # 파싱 결과 검증
            if parsed:
                vendor = parsed["vendor"]
                rows = parsed["rows"]
                total_qty = parsed["total_qty"]
                total_amount = parsed["total_amount"]
                if not rows and not error:
                    error = "❌ CRT 100 / CRT 100 DA 품목을 찾지 못했습니다."

    return render_template_string(
        HTML,
        filename=filename,
        vendor=vendor,
        rows=rows,
        total_qty=total_qty,
        total_amount=total_amount,
        invoice_date_raw=invoice_date_raw,
        invoice_date_kr=invoice_date_kr,
        po_numbers=po_numbers,
        error=error,
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

