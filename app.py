import streamlit as st
import pdfplumber
import re
import pandas as pd
from datetime import datetime
import io
from collections import defaultdict

# ==========================================
# 1. CẤU HÌNH & HÀM HỖ TRỢ
# ==========================================

MONTHS_FULL = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]
MONTHS_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

EXCLUDE_KEYWORDS = [
    'total', 'chuyen khoan', 'transfer', 'deposit', 'paid', 'preauth', 'cọc', 'balance', 
    'tax code', 'ma so thue', 'mst', 'member', 'tel', 'fax', 'mobile', 'phone', 'vat',
    'amount', 'grand', 'net', 'visa', 'master', 'cash'
]

# Danh sách các mã phòng thường gặp cơ bản (Đã update theo file Matrix)
KNOWN_ROOM_TYPES = ['KGB', 'TWB', 'KGA', 'TWA', 'KGAEF', 'TWAEF', 'SKB', 'SKC', 'SKE', 'SKA', 'SUI', 'DLX', 'SUP', 'EXE', 'RB1', 'RB3']

def get_report_date(full_text):
    patterns = [
        r'(?:Filter Date|Date|Ngày|Report Date|Ngày báo cáo|Filer Date)\s*[:=]?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
        r'(\d{2}-\d{2}-\d{2})\s*(?:Filter|Date|Page|\n|$)',
        r'(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',
    ]
    for pat in patterns:
        m = re.search(pat, full_text, re.IGNORECASE)
        if m:
            date_str = m.group(1).strip()
            for sep in ['-', '/']:
                if sep in date_str:
                    parts = date_str.split(sep)
                    if len(parts) == 3:
                        d, mth, y = map(str.strip, parts)
                        if len(y) == 2: y = '20' + y
                        try:
                            return datetime(int(y), int(mth), int(d))
                        except: pass
    return None

def clean_rate(rate_str):
    if not rate_str: return 0
    s = str(rate_str).strip()
    # Loại bỏ đuôi thập phân (ví dụ .00 hoặc ,00 ở cuối cùng)
    s = re.sub(r'[\.,]\d{2}$', '', s)
    # Xóa mọi ký tự không phải số
    s = re.sub(r'[^\d]', '', s)
    if not s: return 0
    return int(s)

def extract_prs_adl_rate(header):
    match_rate = re.search(r'([\d,\.]+)\s+VND', header, re.IGNORECASE)
    rate_str = match_rate.group(1) if match_rate else None
    rate_val = clean_rate(rate_str)
    
    match_pac = re.search(r'\b(\d+)\s+(\d+)\s+(\d+)\b', header)
    if match_pac:
        prs, adl, chl = map(int, match_pac.groups())
    else:
        prs, adl = 0, 0
    return prs, adl, rate_val, rate_str

# ==========================================
# 2. LOGIC CHECK NGÀY 
# ==========================================

def check_line_condition(line, report_date):
    line_lower = line.lower()
    
    arrow_matches = re.finditer(r'(\d{1,2})\s*(?:->|to)\s*(\d{1,2})[/-](\d{1,2})', line_lower)
    for m in arrow_matches:
        d1, d2, m2 = map(int, m.groups())
        try:
            y = report_date.year
            start = datetime(y, m2, d1)
            end = datetime(y, m2, d2)
            if start <= report_date <= end: return 'MATCH'
        except: pass

    range_matches = re.finditer(r'(\d{1,2})[/-](\d{1,2})\s*[-–]\s*(\d{1,2})[/-](\d{1,2})', line_lower)
    for m in range_matches:
        d1, m1, d2, m2 = map(int, m.groups())
        try:
            y = report_date.year
            start = datetime(y, m1, d1)
            end = datetime(y, m2, d2)
            if m1 == 12 and m2 == 1: end = datetime(y+1, m2, d2)
            if start <= report_date <= end: return 'MATCH'
        except: pass

    list_matches = re.finditer(r'\b(\d{1,2}(?:-\d{1,2})+)\b', line_lower)
    for m in list_matches:
        seq_str = m.group(1)
        try:
            days = [int(x) for x in seq_str.split('-')]
            if all(1 <= x <= 31 for x in days):
                if report_date.day in days: return 'MATCH'
        except: pass

    date_matches = re.findall(r'\b(\d{1,2})[/-](\d{1,2})\b', line_lower)
    if date_matches:
        found_match = False
        for d, m in date_matches:
            try:
                if int(m) > 12 or int(d) > 31: continue
                if int(d) == report_date.day and int(m) == report_date.month:
                    found_match = True
            except: pass
        if found_match: return 'MATCH'
        return 'NO_MATCH'

    month_names = '|'.join([m[:3].lower() for m in MONTHS_FULL])
    month_matches = re.finditer(rf'\b({month_names})\S*\s+((?:\d{{1,2}}(?:st|nd|rd|th)?[\s,]+)*\d{{1,2}})', line_lower)
    found_any_date = False
    for mm in month_matches:
        found_any_date = True
        mon_str = mm.group(1)
        days = re.findall(r'\d+', mm.group(2))
        mon_idx = -1
        for i, mname in enumerate(MONTHS_ABBR):
            if mname.lower() == mon_str[:3]:
                mon_idx = i + 1; break   
        if mon_idx == report_date.month:
            for d in days:
                if int(d) == report_date.day: return 'MATCH'
    if found_any_date: return 'NO_MATCH'

    is_weekend = report_date.weekday() >= 5
    is_sat = report_date.weekday() == 5
    is_sun = report_date.weekday() == 6
    if 'sat' in line_lower: return 'MATCH' if is_sat else 'NO_MATCH'
    if 'sun' in line_lower: return 'MATCH' if is_sun else 'NO_MATCH'
    if 'weekend' in line_lower: return 'MATCH' if is_weekend else 'NO_MATCH'
    if 'weekday' in line_lower: return 'MATCH' if not is_weekend else 'NO_MATCH'
    
    return 'NEUTRAL'

# ==========================================
# 3. TÌM GIÁ - BỔ SUNG ĐẾM MỨC GIÁ
# ==========================================

def extract_best_rate(comments, report_date, system_rate_val, room_type=None):
    if not comments: return 0, "N/A", 0
    lines = comments.split('\n')
    candidates = [] 
    
    regex_strict = re.compile(r'([\d,\.]{4,})\s*(?:\+\+|VND|vnd)') 
    regex_prefix = re.compile(r'(?:VND|vnd)\s*([\d,\.]{4,})') 
    regex_loose  = re.compile(r'([\d,\.]{4,})') 

    current_known_types = KNOWN_ROOM_TYPES.copy()
    if room_type and room_type not in current_known_types:
        current_known_types.append(room_type)

    for line in lines:
        line_clean = line.strip()
        if not line_clean: continue
        
        if any(kw in line_clean.lower() for kw in EXCLUDE_KEYWORDS): continue
        
        cond = check_line_condition(line_clean, report_date)
        if cond == 'NO_MATCH': continue 
        
        line_upper = line_clean.upper()
        line_has_room_type = any(rt in line_upper for rt in current_known_types)
        is_room_type_match = False
        
        if room_type and line_has_room_type:
            if room_type not in line_upper:
                continue
            else:
                is_room_type_match = True

        matches = []
        for m in regex_strict.finditer(line_clean): matches.append((m.group(1), 5)) 
        for m in regex_prefix.finditer(line_clean): matches.append((m.group(1), 4)) 
        if not matches:
             for m in regex_loose.finditer(line_clean): matches.append((m.group(1), 2)) 
        
        for raw, base_prio in matches:
            if raw.startswith('0'): continue 
            val = clean_rate(raw)
            
            if val > 20000000 and system_rate_val < 10000000:
                continue
            
            if system_rate_val > 0 and val > system_rate_val * 5:
                continue

            prio = base_prio
            if cond == 'MATCH': 
                prio += 5 
            
            if is_room_type_match:
                prio += 20
            
            if system_rate_val > 0 and abs(val - system_rate_val) < 5000:
                prio += 10

            if 100000 < val < 100000000:
                candidates.append((val, raw, prio))

    if not candidates: return 0, "N/A", 0
    
    # Tính số lượng mức giá ĐỘC LẬP (khác nhau)
    unique_rates = set(val for val, raw, prio in candidates)
    
    candidates.sort(key=lambda x: (x[2], x[0]), reverse=True)
    return candidates[0][0], candidates[0][1], len(unique_rates)

# ==========================================
# 4. XỬ LÝ VÀ PHÂN TÍCH
# ==========================================

def analyze_room_group(room_no, entries, report_date):
    max_adl = 0
    system_rate_str = "0"
    system_rate_val = 0
    all_comments = ""
    valued_lines = [] 
    room_type = None

    for entry in entries:
        m_rt = re.search(r'\d{2}-\d{2}-\d{2}\s+\d{2}-\d{2}-\d{2}\s+([A-Z0-9]{3,6})\s+([A-Z0-9]{3,6})', entry)
        if m_rt and not room_type:
            room_type = m_rt.group(2) 

        if 'Res. Comments' in entry:
            header, _ = entry.split('Res. Comments', 1)
        else:
            header = entry
        
        _, _, rate_val, rate_str = extract_prs_adl_rate(header)
        if rate_val > 0 and system_rate_val == 0:
            system_rate_val = rate_val
            system_rate_str = rate_str

    for entry in entries:
        if 'Res. Comments' in entry:
            header, comments_part = entry.split('Res. Comments', 1)
            comments = comments_part.strip()
        else:
            header, comments = entry, ""

        all_comments += comments + "\n"

        prs, adl, rate_val, rate_str = extract_prs_adl_rate(header)
        max_adl = max(max_adl, adl)

        if prs > 0 or adl > 0 or rate_val > 0:
            valued_lines.append((prs, adl, rate_val))
            
    comm_val, comm_str, num_unique_rates = extract_best_rate(all_comments, report_date, system_rate_val, room_type)
    
    errors = []
    warning = ""
    
    # 1. Báo lỗi chênh lệch giá
    if system_rate_val > 0:
        if comm_val > 0: 
            if abs(system_rate_val - comm_val) > 10:
                errors.append(f"Rate không khớp (Hệ thống: {system_rate_val:,} vs Comments: {comm_val:,})")
        else:
            errors.append("Không tìm thấy rate hợp lệ trong Comments")
            
    # 2. Báo lỗi Share
    if len(valued_lines) > 1:
        errors.append("Lỗi Share: Có Sharer bị dính giá (chọn nhầm Split) hoặc quên set Adults = 0")
        
    # 3. Báo hiệu cảnh báo Check tay nếu có >= 2 mức giá
    if num_unique_rates > 1:
        warning = "⚠️ CẦN CHECK TAY: Comment có nhiều mức giá theo ngày/loại phòng"
        
    # 4. Gộp Status
    if warning:
        status = warning
        if errors:
            status += " | ❌ " + " | ".join(errors)
    else:
        status = "✅ OK" if not errors else "❌ " + " | ".join(errors)
    
    return {
        'Room No': room_no,
        'Room Type': room_type or "N/A", 
        'Adl. Count': max_adl,
        'Rate Amt': system_rate_str,
        'Rate in Comments': comm_str,
        'Check Status': status
    }

def process_pdf(pdf_file):
    with pdfplumber.open(pdf_file) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        
    report_date = get_report_date(full_text)
    if not report_date: report_date = datetime.now()

    room_groups = defaultdict(list)
    current_room = None
    current_entry = ""
    
    for line in full_text.splitlines():
        stripped = line.strip()
        if re.match(r'^\d{4}\s', stripped):
            if current_entry and current_room:
                room_groups[current_room].append(current_entry)
            current_room = stripped[:4]
            current_entry = stripped
        elif current_entry:
            current_entry += "\n" + stripped
            
    if current_entry and current_room:
        room_groups[current_room].append(current_entry)
    
    results = []
    for rn, entries in room_groups.items():
        results.append(analyze_room_group(rn, entries, report_date))
        
    return pd.DataFrame(results), report_date, len(room_groups)

# ==========================================
# 5. GIAO DIỆN STREAMLIT WEB APP
# ==========================================

st.set_page_config(page_title="Pullman VT - Rate Check Auto", page_icon="🏨", layout="wide")

st.title("🏨 Pullman Vung Tau - Rate Check Automation V17")
st.markdown("Công cụ tự động kiểm tra chênh lệch giá hệ thống và ghi chú (Reservation Comments) từ file PDF xuất từ Opera.")

uploaded_file = st.file_uploader("Tải lên file báo cáo Rate Check (PDF)", type=["pdf"])

if uploaded_file is not None:
    with st.spinner('Hệ thống đang phân tích dữ liệu, vui lòng đợi vài giây...'):
        df, report_date, total_rooms = process_pdf(uploaded_file)
        
        if df.empty:
            st.error("Không tìm thấy dữ liệu phòng trong file PDF này.")
        else:
            st.success(f"✅ Hoàn tất! Đã quét {total_rooms} phòng. (Ngày báo cáo nhận diện: {report_date.strftime('%d-%m-%Y')})")
            
            # Lọc ra cả lỗi ❌ và cảnh báo ⚠️
            df_attention = df[df['Check Status'] != '✅ OK']
            
            # Hiển thị số liệu thống kê
            col1, col2 = st.columns(2)
            col1.metric("Tổng số phòng", total_rooms)
            col2.metric("Số phòng cần xử lý (Lỗi & Check Tay)", len(df_attention), delta_color="inverse")
            
            st.markdown("### Danh sách các phòng cần xử lý:")
            if not df_attention.empty:
                st.dataframe(df_attention, use_container_width=True)
            else:
                st.info("Tuyệt vời! Không có phòng nào bị lệch giá, lỗi share hoặc cần check tay.")
                
            # Tạo file Excel trong bộ nhớ để tải về
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name='Tất cả kết quả', index=False)
                if not df_attention.empty:
                    df_attention.to_excel(writer, sheet_name='Cần xử lý', index=False)
            processed_data = output.getvalue()
            
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            st.download_button(
                label="📥 TẢI FILE KẾT QUẢ (EXCEL)",
                data=processed_data,
                file_name=f"KET_QUA_RATE_CHECK_{ts}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )