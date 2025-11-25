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
    Paragon 인보이스에서 CRT 100 / CRT 100 DA 품목의 수량과 금액을 추출하는 파서.
    pdfplumber로 추출된 세로 레이아웃(1.00 / CRT100 / Regular / CRT 100 ...)을 기준으로 동작한다.
    """
    lines = text.splitlines()

    # 1) "1.00" 바로 뒤 2~3줄 안에 "CRT100" 이 나오는 경우를 한 품목 블록의 시작으로 본다.
    qty_indices = []
    for i, line in enumerate(lines):
        if line.strip() == "1.00":
            for j in range(i + 1, min(i + 4, len(lines))):
                if "CRT100" in lines[j]:
                    qty_indices.append(i)
                    break

    grouped = defaultdict(lambda: {"qty": 0.0, "amount": 0.0})

    # 2) 각 블록별로 마지막 금액(예: 58.00)을 찾아서 최종 금액으로 사용한다.
    for pos, qidx in enumerate(qty_indices):
        block_start = qidx
        block_end = qty_indices[pos + 1] if pos + 1 < len(qty_indices) else len(lines)
        block_lines = lines[block_start:block_end]
        block_text_upper = "\n".join(block_lines).upper()

        # 제품 타입 분류 (CRT 100 vs CRT 100 DA)
        if re.search(r"CRT\s*100\s*DA", block_text_upper):
            key = "CRT 100 DA"
        else:
            key = "CRT 100"

        # 수량: 시작 줄의 1.00 기준 (나중에 수량이 달라지면 여기만 조정하면 됨)
        try:
            qty_val = float(lines[qidx].strip())
        except ValueError:
            qty_val = 1.0

        # 블록 내부에서 소수점 금액 패턴 찾기 (마지막 값을 총액으로 사용)
        amount_candidates = []
        for k in range(block_start + 1, block_end):
            num_str = lines[k].strip()
            if re.match(r"^\d[\d,]*\.\d{2}$", num_str):
                amount_candidates.append(float(num_str.replace(",", "")))

        final_amount = amount_candidates[-1] if amount_candidates else 0.0

        grouped[key]["qty"] += qty_val
        grouped[key]["amount"] += final_amount

    # 3) 결과 rows 구성
    rows = []
    for key in ["CRT 100", "CRT 100 DA"]:
        data = grouped.get(key)
        if not data:
            continue
        q = data["qty"]
        qty_display = int(q) if float(q).is_integer() else q
        rows.append(
            {
                "item": key,
                "qty": qty_display,
                "amount": data["amount"],
            }
        )

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
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <title>Invoice Genie</title>
  <!-- Pretendard 웹폰트 -->
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/pretendard/dist/web/static/pretendard.css" />
  <style>
    :root {
      --bg: #f9f5ff;
      --card-bg: #ffffff;
      --primary: #6d4aff;
      --primary-soft: #f0e9ff;
      --primary-strong: #4b2ee8;
      --text-main: #241b3a;
      --text-muted: #7b7394;
      --border-soft: #e1d7ff;
      --error-bg: #ffe9ea;
      --error-border: #ff9ca8;
      --error-text: #b3263e;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      padding: 40px 24px;
      background: var(--bg);
      font-family: "Pretendard", -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
      color: var(--text-main);
      display: flex;
      justify-content: center;
    }

    .container {
      width: 100%;
      max-width: 980px;
    }

    .header {
      margin-bottom: 18px;
    }

    h1 {
      margin: 0 0 6px 0;
      font-size: 40px;
      font-weight: 800;
      letter-spacing: -0.03em;
    }

    .subtitle {
      font-size: 15px;
      color: var(--text-muted);
    }

    .card {
      margin-top: 12px;
      padding: 26px 26px 30px;
      border-radius: 24px;
      background: var(--card-bg);
      box-shadow: 0 16px 40px rgba(23, 8, 64, 0.09);
      border: 1px solid var(--border-soft);
    }

    .drop {
      position: relative;
      border-radius: 20px;
      border: 2px dashed #c3adff;
      background: var(--primary-soft);
      padding: 40px 28px;
      text-align: center;
      cursor: pointer;
      transition: background 0.18s ease, border-color 0.18s ease, transform 0.1s ease;
      font-size: 16px;
      color: var(--text-muted);
    }

    .drop.dragover {
      background: #e4d6ff;
      border-color: var(--primary);
      transform: translateY(-1px);
    }

    .drop input[type="file"] {
      position: absolute;
      inset: 0;
      opacity: 0;
      cursor: pointer;
    }

    .filename {
      margin-top: 10px;
      font-size: 14px;
      color: var(--text-muted);
    }

    .filename b {
      color: var(--primary-strong);
    }

    .actions {
      margin-top: 18px;
      display: flex;
      justify-content: flex-start;
    }

    button {
      padding: 11px 32px;
      font-size: 16px;
      border-radius: 999px;
      border: none;
      cursor: pointer;
      background: linear-gradient(135deg, var(--primary), var(--primary-strong));
      color: #ffffff;
      font-weight: 600;
      letter-spacing: 0.02em;
      box-shadow: 0 10px 26px rgba(91, 67, 206, 0.25);
    }

    button:hover {
      filter: brightness(1.04);
      box-shadow: 0 12px 32px rgba(91, 67, 206, 0.3);
    }

    .result {
      margin-top: 26px;
      padding: 22px 22px 24px;
      border-radius: 20px;
      background: #fcfaff;
      border: 1px solid var(--border-soft);
      font-size: 15px;
    }

    .result.err {
      background: var(--error-bg);
      border-color: var(--error-border);
      color: var(--error-text);
    }

    .meta {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 10px 24px;
      margin-bottom: 14px;
    }

    .meta-row span.label {
      display: inline-block;
      min-width: 96px;
      font-weight: 600;
      color: var(--text-main);
    }

    .meta-row span.value {
      color: var(--primary-strong);
      font-weight: 500;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      margin-top: 10px;
      font-size: 14px;
    }

    th, td {
      padding: 8px 10px;
      border-bottom: 1px solid #e4e0f5;
      text-align: center;
    }

    th {
      font-weight: 600;
      color: var(--text-muted);
      background: #f7f3ff;
    }

    tfoot td {
      font-weight: 700;
      border-top: 1px solid #d5cbff;
      background: #f6f1ff;
    }

    .empty-msg {
      margin-top: 4px;
      font-size: 14px;
      color: var(--text-muted);
    }
  </style>
</head>
<body>
  <div class="container">
    <header class="header">
      <h1>Invoice Genie</h1>
      <div class="subtitle">인보이스 PDF에서 제품 정보, 수량, 금액 등을 자동으로 분석해주는 도구입니다.</div>
    </header>

    <div class="card">
      <form method="POST" enctype="multipart/form-data">
        <div class="drop" id="dropZone" data-filename="{{ filename or '' }}">
          <span id="dropText">
            {% if filename %}
              ✅ {{ filename }} 업로드 준비 완료
            {% else %}
              PDF 파일을 드래그하거나 클릭해 업로드하세요
            {% endif %}
          </span>
          <input id="fileInput" type="file" name="file" accept="application/pdf" />
        </div>

        {% if filename %}
          <div class="filename">📎 업로드된 파일: <b>{{ filename }}</b></div>
        {% endif %}

        <div class="actions">
          <button type="submit">Analyze</button>
        </div>
      </form>

      {% if error %}
        <div class="result err">{{ error }}</div>
      {% elif rows %}
        <div class="result">
          <div class="meta">
            <div class="meta-row">
              <span class="label">Vendor</span>
              <span class="value">{{ vendor }}</span>
            </div>
            {% if invoice_date_kr %}
            <div class="meta-row">
              <span class="label">Invoice Date</span>
              <span class="value">{{ invoice_date_kr }}</span>
            </div>
            {% endif %}
            {% if po_numbers %}
            <div class="meta-row" style="grid-column: 1 / -1;">
              <span class="label">Ref PO</span>
              <span class="value">{{ po_numbers }}</span>
            </div>
            {% endif %}
          </div>

          {% if rows %}
          <table>
            <thead>
              <tr>
                <th>Item</th>
                <th>Quantity</th>
                <th>Total Amount (USD)</th>
              </tr>
            </thead>
            <tbody>
              {% for row in rows %}
              <tr>
                <td>{{ row.item }}</td>
                <td>{{ row.qty }}</td>
                <td>{{ "{:,.2f}".format(row.amount) }}</td>
              </tr>
              {% endfor %}
            </tbody>
            <tfoot>
              <tr>
                <td>합계</td>
                <td>{{ total_qty }}</td>
                <td>{{ "{:,.2f}".format(total_amount) }}</td>
              </tr>
            </tfoot>
          </table>
          {% else %}
            <div class="empty-msg">분석된 품목이 없습니다.</div>
          {% endif %}
        </div>
      {% endif %}
    </div>
  </div>

  <script>
    const dz = document.getElementById("dropZone");
    const fi = document.getElementById("fileInput");
    const dropText = document.getElementById("dropText");

    function setFileNameFromDataAttr() {
      const existing = dz.getAttribute("data-filename");
      if (existing) {
        dropText.textContent = `✅ ${existing} 업로드 준비 완료`;
      }
    }

    setFileNameFromDataAttr();

    function handleFileSelect(files) {
      if (!files || files.length === 0) return;
      const name = files[0].name;
      dropText.textContent = `✅ ${name} 업로드 준비 완료`;
    }

    dz.addEventListener("dragover", (e) => {
      e.preventDefault();
      dz.classList.add("dragover");
    });

    dz.addEventListener("dragleave", () => {
      dz.classList.remove("dragover");
    });

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

