# -*- coding: utf-8 -*-
"""
Tapu PDF Analiz Aracı
39 adet tapu kaydını okur, analiz eder ve interaktif HTML raporu üretir.
"""

import os
import re
import json
import sys
import pdfplumber
from pathlib import Path
from collections import defaultdict

# Encoding fix
sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent
TAPU_DIR = r"C:\Users\PC\OneDrive\Masaüstü\Tapu"
OUTPUT_HTML = str(BASE_DIR / "tapu_rapor.html")


# ─────────────────────────────────────────────────────────
# PDF PARSER
# ─────────────────────────────────────────────────────────

def clean(s):
    if not s:
        return ""
    return re.sub(r'\s+', ' ', str(s)).strip()

def preprocess_text(text):
  lines = text.splitlines()
  compact_lines = []

  for line in lines:
    line = clean(line)
    if not line:
      continue
    if re.fullmatch(r'[A-Za-zÇĞİÖŞÜçğıöşü]', line):
      continue
    compact_lines.append(line)

  merged_lines = []
  index = 0
  while index < len(compact_lines):
    line = compact_lines[index]
    if re.search(r'\(SN[G]?:\d{7,12}\)', line) and not re.search(r'\d{1,12}/\d{1,12}\s+[\d.,]+\s+[\d.,]+', line):
      look_ahead = index + 1
      while look_ahead < len(compact_lines) and look_ahead <= index + 2:
        next_line = compact_lines[look_ahead]
        if re.search(r'\(SN[G]?:\d{7,12}\)', next_line):
          break
        if len(next_line) < 150:
          line = f"{line} {next_line}"
          if re.search(r'\d{1,12}/\d{1,12}\s+[\d.,]+\s+[\d.,]+', line):
            index = look_ahead
            break
        look_ahead += 1
      else:
        index = look_ahead - 1
    merged_lines.append(line)
    index += 1

  return "\n".join(merged_lines)

def extract_field(text, *patterns):
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if m:
            return clean(m.group(1))
    return ""

def parse_float_safe(value):
  try:
    return float(str(value).replace(',', '.'))
  except (TypeError, ValueError):
    return 0.0

def normalize_percent_values(values, target=100.0):
  if not values:
    return []
  total = sum(parse_float_safe(value) for value in values)
  if total <= 0:
    even_value = round(target / len(values), 5)
    normalized = [even_value for _ in values]
    normalized[-1] = round(target - sum(normalized[:-1]), 5)
    return normalized

  normalized = []
  consumed = 0.0
  for index, value in enumerate(values):
    if index == len(values) - 1:
      normalized.append(round(target - consumed, 5))
      continue
    current_value = round((parse_float_safe(value) / total) * target, 5)
    normalized.append(current_value)
    consumed += current_value
  return normalized

def normalize_owner_shares(owners):
  if not owners:
    return owners, 0.0, 0.0

  raw_totals = [owner.get("yuzde_toplam", owner.get("yuzde", 0.0)) for owner in owners]
  raw_sum = round(sum(parse_float_safe(value) for value in raw_totals), 5)
  normalized_totals = normalize_percent_values(raw_totals, 100.0)
  normalized_owners = []

  for index, owner in enumerate(owners):
    hisseler = owner.get("hisseler") or [{
      "hisse": owner.get("hisse", ""),
      "m2": owner.get("m2_toplam", owner.get("m2", 0.0)),
      "yuzde": owner.get("yuzde_toplam", owner.get("yuzde", 0.0)),
    }]
    raw_share_values = [share.get("yuzde", 0.0) for share in hisseler]
    normalized_shares = normalize_percent_values(raw_share_values, normalized_totals[index])
    owner_copy = dict(owner)
    owner_copy["ham_yuzde_toplam"] = round(parse_float_safe(owner.get("yuzde_toplam", owner.get("yuzde", 0.0))), 5)
    owner_copy["yuzde_toplam"] = normalized_totals[index]
    owner_copy["hisseler"] = []

    for share_index, share in enumerate(hisseler):
      share_copy = dict(share)
      share_copy["ham_yuzde"] = round(parse_float_safe(share.get("yuzde", 0.0)), 5)
      share_copy["yuzde"] = normalized_shares[share_index]
      owner_copy["hisseler"].append(share_copy)

    normalized_owners.append(owner_copy)

  normalized_owners.sort(key=lambda owner: (-owner["yuzde_toplam"], owner["ad"]))
  return normalized_owners, raw_sum, 100.0

def parse_owners(text, total_m2=0):
  owners = []
  seen = set()
  owner_pattern = re.compile(r'\(SN[G]?:(\d{7,12})\)\s+([^\n\r:]{2,80}?)\s*:', re.UNICODE)

  for match in owner_pattern.finditer(text):
    serial_no = match.group(1)
    owner_name = clean(match.group(2))
    owner_name = ' '.join(part for part in owner_name.split() if len(part) > 1)
    if len(owner_name) < 2:
      continue

    forward_text = text[match.end():match.end() + 700]
    share_match = re.search(r'(\d{1,12})/(\d{1,12})\s+([\d.,]+)\s+([\d.,]+)', forward_text)
    if not share_match:
      continue

    pay = int(share_match.group(1))
    payda = int(share_match.group(2))
    owner_m2 = parse_float_safe(share_match.group(3))
    share_total_m2 = parse_float_safe(share_match.group(4)) or total_m2 or 0

    if pay <= 0 or payda <= 0 or pay > payda:
      continue
    if share_total_m2 <= 0:
      continue
    if owner_m2 <= 0:
      owner_m2 = share_total_m2 * (pay / payda)
    if owner_m2 > share_total_m2 * 1.01:
      continue

    share_ratio = f"{pay}/{payda}"
    key = (serial_no, share_ratio)
    if key in seen:
      continue
    seen.add(key)

    share_percent = round((owner_m2 / share_total_m2) * 100 if share_total_m2 else (pay / payda * 100), 5)
    owners.append({
      "sn": serial_no,
      "ad": owner_name,
      "hisse": share_ratio,
      "m2": round(owner_m2, 4),
      "yuzde": share_percent,
    })

  owners_by_sn = {}
  for owner in owners:
    serial_no = owner["sn"]
    if serial_no not in owners_by_sn:
      owners_by_sn[serial_no] = {
        "sn": serial_no,
        "ad": owner["ad"],
        "hisse": owner["hisse"],
        "m2_toplam": round(owner["m2"], 4),
        "yuzde_toplam": round(owner["yuzde"], 5),
        "hisseler": [{
          "hisse": owner["hisse"],
          "m2": round(owner["m2"], 4),
          "yuzde": round(owner["yuzde"], 5),
        }],
      }
      continue

    owners_by_sn[serial_no]["m2_toplam"] = round(owners_by_sn[serial_no]["m2_toplam"] + owner["m2"], 4)
    owners_by_sn[serial_no]["yuzde_toplam"] = round(owners_by_sn[serial_no]["yuzde_toplam"] + owner["yuzde"], 5)
    owners_by_sn[serial_no]["hisseler"].append({
      "hisse": owner["hisse"],
      "m2": round(owner["m2"], 4),
      "yuzde": round(owner["yuzde"], 5),
    })

  return sorted(owners_by_sn.values(), key=lambda owner: (-owner["yuzde_toplam"], owner["ad"]))

def parse_serhler(text):
    serhler = []
    # Find Beyan/Şerh/İrtifak lines
    pattern = re.compile(r'(Beyan|Şerh|İrtifak|B\nBeyan)\s+(.*?)(?=Beyan|Şerh|İrtifak|MÜLKİYET|\Z)', re.DOTALL | re.UNICODE)
    for m in pattern.finditer(text):
        tip = "Beyan" if "Beyan" in m.group(1) else m.group(1).strip()
        aciklama = clean(m.group(2))[:300]
        if aciklama and len(aciklama) > 10:
            serhler.append({"tip": tip, "aciklama": aciklama})
    return serhler[:10]  # max 10

def parse_tapu_pdf(filepath):
    filename = os.path.basename(filepath)
    result = {
        "dosya": filename,
        "dosya_no": filename.split(")")[0].strip() if ")" in filename else "",
        "tam_metin": "",
        "hata": None,
    }

    try:
        full_text = []
        with pdfplumber.open(filepath) as pdf:
            result["sayfa_sayisi"] = len(pdf.pages)
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    full_text.append(t)

        raw_text = "\n".join(full_text)
        text = preprocess_text(raw_text)
        result["tam_metin"] = text[:5000]  # store first 5000 chars for display

        # ── Core fields ──
        result["kimlik_no"] = extract_field(text,
            r'Taşınmaz Kimlik No:\s*(\d+)',
            r'Kimlik No[:\s]+(\d+)')

        result["ada_parsel"] = extract_field(text,
          r'Ada/Parsel:\s*([^\n]+)')
        if not result["ada_parsel"]:
          ada_parsel_match = re.search(r'Ada[:\s]+([\w/-]+)\s+Parsel[:\s]+([\w/-]+)', text, re.IGNORECASE)
          if ada_parsel_match:
            result["ada_parsel"] = f"{ada_parsel_match.group(1)}/{ada_parsel_match.group(2)}"

        result["yuzolcum"] = extract_field(text,
            r'AT Yüzölçüm\(m2\):\s*([\d.,]+)',
            r'Yüzölçüm\s*\(m2\)[:\s]*([\d.,]+)',
            r'Yüzölçüm[:\s]*([\d.,]+)')
        # numeric
        try:
            result["yuzolcum_sayi"] = float(result["yuzolcum"].replace(',', '.')) if result["yuzolcum"] else 0
        except:
            result["yuzolcum_sayi"] = 0

        result["nitelik"] = extract_field(text,
            r'Ana Taşınmaz Nitelik:\s*([^\n]+)',
            r'Nitelik[:\s]+([A-ZÇĞİÖŞÜa-zçğışöü /]+)')

        result["il_ilce"] = extract_field(text,
            r'İl/İlçe:\s*([^\n]+)',
            r'İl[:\s]+([^\n]+)')
        if result["il_ilce"]:
          result["il_ilce"] = re.split(r'Bağımsız Bölüm', result["il_ilce"], maxsplit=1)[0].strip(' -')

        result["kurum"] = extract_field(text,
            r'Kurum Adı:\s*([^\n]+)')

        result["mahalle"] = extract_field(text,
            r'Mahalle/Köy Adı:\s*([^\n]+)',
            r'Mahalle[:\s]+([^\n]+)')

        result["mevkii"] = extract_field(text,
            r'Mevkii:\s*([^\n]+)')

        result["cilt_sayfa"] = extract_field(text,
            r'Cilt/Sayfa No:\s*([^\n]+)')

        result["kayit_durum"] = extract_field(text,
            r'Kayıt Durum:\s*([^\n]+)',
            r'Kayıt[:\s]+(Aktif|Pasif)')
        if not result["kayit_durum"]:
            result["kayit_durum"] = "Aktif" if "Aktif" in text else "Bilinmiyor"

        result["zemin_tipi"] = extract_field(text,
            r'Zemin Tipi:\s*([^\n]+)')

        # Owners
        result["malikler"] = parse_owners(text, result["yuzolcum_sayi"])
        result["malikler"], result["ham_hisse_toplam"], result["hisse_toplam"] = normalize_owner_shares(result["malikler"])
        result["malik_sayisi"] = len(result["malikler"])

        # Şerh/Beyan
        result["serhler"] = parse_serhler(text)
        result["serh_sayisi"] = len(result["serhler"])

        # Belge tarihi
        tarih_m = re.search(r'Tarih:\s*(\d{2}-\d{2}-\d{4})', text)
        result["belge_tarihi"] = tarih_m.group(1) if tarih_m else ""

        # İzale-i şuyu (ortaklığın giderilmesi davası)
        result["izale_var"] = "İzale-i Şuy" in text or "izale-i şuy" in text.lower()

        # Kayyum / haciz / tedbir keywords
        result["haciz_var"] = bool(re.search(r'haciz|ihtiyati tedbir', text, re.IGNORECASE))
        result["imar_aykiri"] = "İmar Mevzuatına Aykırı" in text

    except Exception as e:
        result["hata"] = str(e)

    return result


# ─────────────────────────────────────────────────────────
# MAIN: Read all PDFs
# ─────────────────────────────────────────────────────────

def load_all_pdfs():
    files = sorted([
        os.path.join(TAPU_DIR, f)
        for f in os.listdir(TAPU_DIR)
        if f.lower().endswith('.pdf')
    ])
    print(f"[INFO] {len(files)} PDF bulundu...")
    results = []
    for i, fp in enumerate(files):
        fname = os.path.basename(fp)
        print(f"  [{i+1:02d}/{len(files)}] {fname[:70]}")
        r = parse_tapu_pdf(fp)
        results.append(r)
    return results


# ─────────────────────────────────────────────────────────
# STATISTICS
# ─────────────────────────────────────────────────────────

def compute_stats(data):
    stats = {}
    stats["toplam_tasinmaz"] = len(data)
    stats["toplam_alan_m2"] = sum(d["yuzolcum_sayi"] for d in data)
    stats["toplam_alan_m2_fmt"] = f"{stats['toplam_alan_m2']:,.2f}"

    # By type
    by_nitelik = defaultdict(list)
    for d in data:
        nik = d.get("nitelik", "Bilinmiyor").upper().strip() or "Bilinmiyor"
        # Normalize
        if "TARLA" in nik:
            nik = "TARLA"
        elif "ARSA" in nik:
            nik = "ARSA"
        elif "DÜKKAN" in nik or "DUKKAN" in nik:
            nik = "DÜKKAN"
        elif "EV" in nik or "KONUT" in nik or "AVLU" in nik:
            nik = "KONUT/EV"
        elif "YOL" in nik:
            nik = "YOL"
        elif "SAMANLIK" in nik or "SAMANLIK" in nik:
            nik = "SAMANLIK"
        elif "BAHÇELİ" in nik or "BAHCELI" in nik:
            nik = "BAHÇELİ"
        d["nitelik_norm"] = nik
        by_nitelik[nik].append(d)

    stats["nitelik_dagilimi"] = {
        k: {"adet": len(v), "toplam_m2": sum(x["yuzolcum_sayi"] for x in v)}
        for k, v in sorted(by_nitelik.items(), key=lambda x: -len(x[1]))
    }

    # By district
    by_ilce = defaultdict(list)
    for d in data:
        ilce = d.get("il_ilce", "Bilinmiyor").strip() or "Bilinmiyor"
        by_ilce[ilce].append(d)
    stats["ilce_dagilimi"] = {
        k: len(v) for k, v in sorted(by_ilce.items(), key=lambda x: -len(x[1]))
    }

    stats["izale_sayisi"] = sum(1 for d in data if d.get("izale_var"))
    stats["haciz_sayisi"] = sum(1 for d in data if d.get("haciz_var"))
    stats["imar_aykiri_sayisi"] = sum(1 for d in data if d.get("imar_aykiri"))
    stats["hatali_pdf"] = sum(1 for d in data if d.get("hata"))

    # Top 5 largest
    sorted_by_area = sorted(data, key=lambda x: x["yuzolcum_sayi"], reverse=True)
    stats["en_buyuk_5"] = [
        {"dosya": d["dosya"], "alan": d["yuzolcum_sayi"], "nitelik": d.get("nitelik_norm","?")}
        for d in sorted_by_area[:5]
    ]

    # Malik frequency (common owners across properties)
    malik_count = defaultdict(int)
    malik_ozet = {}
    for d in data:
        for m in d.get("malikler", []):
            name = m["ad"].strip()
            if len(name) > 3:
                malik_count[name] += 1
                if name not in malik_ozet:
                    malik_ozet[name] = {
                        "ad": name,
                        "tasinmaz_sayisi": 0,
                        "toplam_m2": 0.0,
                        "ortalama_yuzde": 0.0,
                        "toplam_yuzde": 0.0,
                        "ornekler": [],
                    }
                malik_ozet[name]["tasinmaz_sayisi"] += 1
                malik_ozet[name]["toplam_m2"] += m.get("m2_toplam", 0.0)
                malik_ozet[name]["toplam_yuzde"] += m.get("yuzde_toplam", 0.0)
                malik_ozet[name]["ham_toplam_yuzde"] = malik_ozet[name].get("ham_toplam_yuzde", 0.0) + m.get("ham_yuzde_toplam", m.get("yuzde_toplam", 0.0))
                if len(malik_ozet[name]["ornekler"]) < 4:
                    malik_ozet[name]["ornekler"].append(d.get("ada_parsel") or d.get("dosya_no") or d.get("dosya"))
    stats["en_cok_malik"] = sorted(malik_count.items(), key=lambda x: -x[1])[:10]
    stats["malik_ozet"] = []
    for malik in malik_ozet.values():
        count = malik["tasinmaz_sayisi"] or 1
        malik["toplam_m2"] = round(malik["toplam_m2"], 2)
        malik["ortalama_yuzde"] = round(malik["toplam_yuzde"] / count, 4)
        malik["ham_ortalama_yuzde"] = round(malik.get("ham_toplam_yuzde", 0.0) / count, 4)
        malik.pop("toplam_yuzde", None)
        malik.pop("ham_toplam_yuzde", None)
        stats["malik_ozet"].append(malik)
    stats["malik_ozet"] = sorted(
        stats["malik_ozet"],
        key=lambda malik: (-malik["tasinmaz_sayisi"], -malik["toplam_m2"], malik["ad"])
    )[:20]

    return stats


# ─────────────────────────────────────────────────────────
# HTML GENERATOR
# ─────────────────────────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tapu Analiz Raporu</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {
    --primary: #1e3a5f;
    --accent: #c0392b;
    --accent2: #27ae60;
    --bg: #f0f2f5;
    --card: #ffffff;
    --border: #dde1e7;
    --text: #2c3e50;
    --muted: #7f8c8d;
    --shadow: 0 2px 12px rgba(0,0,0,0.09);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', Arial, sans-serif; background: var(--bg); color: var(--text); }

  /* NAV */
  nav {
    background: var(--primary);
    color: white;
    padding: 0 28px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 56px;
    position: sticky; top: 0; z-index: 100;
    box-shadow: 0 2px 8px rgba(0,0,0,0.3);
  }
  nav .logo { font-size: 1.2rem; font-weight: 700; letter-spacing: 0.5px; }
  nav .nav-tabs { display: flex; gap: 4px; }
  nav .nav-tab {
    padding: 8px 16px; border-radius: 6px; cursor: pointer;
    font-size: 0.88rem; font-weight: 500;
    transition: background 0.2s; color: rgba(255,255,255,0.85);
  }
  nav .nav-tab:hover, nav .nav-tab.active { background: rgba(255,255,255,0.18); color: white; }

  /* MAIN */
  .container { max-width: 1300px; margin: 0 auto; padding: 28px 20px; }

  /* SECTION */
  .section { display: none; }
  .section.active { display: block; }

  /* SUMMARY CARDS */
  .summary-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px; margin-bottom: 28px;
  }
  .summary-card {
    background: var(--card); border-radius: 12px; padding: 20px 18px;
    box-shadow: var(--shadow); border-left: 4px solid var(--primary);
    transition: transform 0.2s;
  }
  .summary-card:hover { transform: translateY(-2px); }
  .summary-card .label { font-size: 0.78rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
  .summary-card .value { font-size: 1.8rem; font-weight: 700; color: var(--primary); }
  .summary-card .sub { font-size: 0.8rem; color: var(--muted); margin-top: 4px; }
  .summary-card.danger { border-left-color: var(--accent); }
  .summary-card.danger .value { color: var(--accent); }
  .summary-card.success { border-left-color: var(--accent2); }

  /* CHARTS */
  .charts-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 28px;
  }
  @media(max-width: 700px) { .charts-grid { grid-template-columns: 1fr; } }
  .chart-card {
    background: var(--card); border-radius: 12px; padding: 20px;
    box-shadow: var(--shadow);
  }
  .chart-card h3 { font-size: 0.95rem; color: var(--muted); margin-bottom: 16px; font-weight: 600; }
  .chart-wrap { position: relative; height: 280px; }

  /* PROPERTY LIST */
  .filter-bar {
    background: var(--card); border-radius: 10px; padding: 16px 20px;
    box-shadow: var(--shadow); margin-bottom: 20px;
    display: flex; gap: 12px; flex-wrap: wrap; align-items: center;
  }
  .filter-bar input, .filter-bar select {
    padding: 9px 14px; border: 1px solid var(--border); border-radius: 7px;
    font-size: 0.9rem; color: var(--text); background: white;
    outline: none; transition: border 0.2s;
  }
  .filter-bar input { flex: 1; min-width: 200px; }
  .filter-bar input:focus, .filter-bar select:focus { border-color: var(--primary); }
  .filter-bar .result-count { margin-left: auto; font-size: 0.85rem; color: var(--muted); white-space: nowrap; }

  .prop-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(310px, 1fr));
    gap: 16px;
  }
  .prop-card {
    background: var(--card); border-radius: 12px; padding: 18px;
    box-shadow: var(--shadow); cursor: pointer;
    transition: transform 0.15s, box-shadow 0.15s;
    border: 2px solid transparent;
  }
  .prop-card:hover { transform: translateY(-3px); box-shadow: 0 6px 20px rgba(0,0,0,0.13); border-color: var(--primary); }
  .prop-card .card-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; }
  .prop-card .card-no { font-size: 0.75rem; color: var(--muted); }
  .prop-card .badge {
    font-size: 0.72rem; padding: 3px 9px; border-radius: 20px; font-weight: 600;
    background: #eaf0fb; color: var(--primary);
  }
  .prop-card .badge.TARLA { background: #e8f5e9; color: #2e7d32; }
  .prop-card .badge.ARSA { background: #fff3e0; color: #e65100; }
  .prop-card .badge.YOL { background: #f3e5f5; color: #6a1b9a; }
  .prop-card .badge.DÜKKAN { background: #e3f2fd; color: #0d47a1; }
  .prop-card .badge.SAMANLIK { background: #fce4ec; color: #880e4f; }
  .prop-card h4 { font-size: 0.97rem; font-weight: 700; margin-bottom: 6px; line-height: 1.3; }
  .prop-card .meta { font-size: 0.82rem; color: var(--muted); margin-bottom: 3px; }
  .prop-card .area { font-size: 1.3rem; font-weight: 700; color: var(--primary); margin-top: 8px; }
  .prop-card .tags { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px; }
  .prop-card .tag {
    font-size: 0.7rem; padding: 2px 8px; border-radius: 10px; font-weight: 500;
  }
  .prop-card .tag.izale { background: #ffebee; color: #c62828; }
  .prop-card .tag.haciz { background: #fff8e1; color: #f57f17; }
  .prop-card .tag.imar { background: #fce4ec; color: #880e4f; }
  .prop-card .tag.serh { background: #e8eaf6; color: #283593; }

  /* MODAL */
  .modal-overlay {
    display: none; position: fixed; inset: 0;
    background: rgba(0,0,0,0.5); z-index: 1000;
    align-items: center; justify-content: center;
    padding: 20px;
  }
  .modal-overlay.open { display: flex; }
  .modal {
    background: white; border-radius: 14px; width: 100%; max-width: 780px;
    max-height: 90vh; overflow-y: auto; box-shadow: 0 20px 60px rgba(0,0,0,0.3);
  }
  .modal-header {
    background: var(--primary); color: white;
    padding: 20px 24px; border-radius: 14px 14px 0 0;
    display: flex; justify-content: space-between; align-items: center;
    position: sticky; top: 0;
  }
  .modal-header h2 { font-size: 1.1rem; }
  .modal-close { cursor: pointer; font-size: 1.5rem; line-height: 1; opacity: 0.8; }
  .modal-close:hover { opacity: 1; }
  .modal-body { padding: 24px; }
  .modal-section { margin-bottom: 20px; }
  .modal-section h3 { font-size: 0.85rem; text-transform: uppercase; color: var(--muted); letter-spacing: 1px; margin-bottom: 12px; padding-bottom: 6px; border-bottom: 1px solid var(--border); }
  .info-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 20px; }
  .info-row { display: flex; flex-direction: column; gap: 2px; }
  .info-row .key { font-size: 0.75rem; color: var(--muted); }
  .info-row .val { font-size: 0.92rem; font-weight: 600; }
  .malik-list { list-style: none; }
  .malik-list li { padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 0.88rem; display: flex; justify-content: space-between; }
  .malik-list li:last-child { border-bottom: none; }
  .serh-list { list-style: none; }
  .serh-item { padding: 10px 12px; background: #fff9f9; border-left: 3px solid var(--accent); border-radius: 0 6px 6px 0; margin-bottom: 8px; font-size: 0.84rem; line-height: 1.5; }
  .alert-box { background: #ffebee; border: 1px solid #ffcdd2; border-radius: 8px; padding: 12px 16px; margin-bottom: 8px; font-size: 0.87rem; color: #c62828; }

  /* ANALYSIS */
  .analysis-section { background: var(--card); border-radius: 12px; padding: 24px; box-shadow: var(--shadow); margin-bottom: 20px; }
  .analysis-section h2 { font-size: 1.05rem; font-weight: 700; margin-bottom: 16px; color: var(--primary); }
  .insight { padding: 12px 16px; border-left: 3px solid var(--primary); background: #f8f9fa; border-radius: 0 8px 8px 0; margin-bottom: 10px; font-size: 0.9rem; line-height: 1.6; }
  .insight.warning { border-left-color: #e67e22; background: #fffaf5; }
  .insight.danger { border-left-color: var(--accent); background: #fff9f9; }
  .insight.success { border-left-color: var(--accent2); background: #f0fff4; }
  .owner-table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
  .owner-table th { background: var(--primary); color: white; padding: 10px 14px; text-align: left; font-size: 0.8rem; }
  .owner-table td { padding: 9px 14px; border-bottom: 1px solid var(--border); }
  .owner-table tr:hover td { background: #f5f7ff; }
  .progress-bar { background: #e0e0e0; border-radius: 4px; height: 8px; margin-top: 4px; }
  .progress-bar .fill { height: 8px; border-radius: 4px; background: var(--primary); transition: width 0.5s; }

  footer { text-align: center; padding: 24px; color: var(--muted); font-size: 0.8rem; }
</style>
</head>
<body>

<nav>
  <div class="logo">📋 Tapu Analiz Sistemi</div>
  <div class="nav-tabs">
    <div class="nav-tab active" onclick="showSection('ozet')">Özet</div>
    <div class="nav-tab" onclick="showSection('tasinmazlar')">Taşınmazlar</div>
    <div class="nav-tab" onclick="showSection('analiz')">Ortak Analiz</div>
  </div>
</nav>

<div class="container">

  <!-- ÖZET SECTION -->
  <div id="sec-ozet" class="section active">
    <div class="summary-grid">
      <div class="summary-card">
        <div class="label">Toplam Taşınmaz</div>
        <div class="value" id="stat-toplam"></div>
        <div class="sub">PDF Kaydı</div>
      </div>
      <div class="summary-card success">
        <div class="label">Toplam Alan</div>
        <div class="value" id="stat-alan"></div>
        <div class="sub">m²</div>
      </div>
      <div class="summary-card">
        <div class="label">Farklı Tür</div>
        <div class="value" id="stat-tur"></div>
        <div class="sub">Nitelik</div>
      </div>
      <div class="summary-card danger">
        <div class="label">İzale Davası</div>
        <div class="value" id="stat-izale"></div>
        <div class="sub">Taşınmaz</div>
      </div>
      <div class="summary-card danger">
        <div class="label">Haciz/Tedbir</div>
        <div class="value" id="stat-haciz"></div>
        <div class="sub">Taşınmaz</div>
      </div>
      <div class="summary-card danger">
        <div class="label">İmar Aykırı</div>
        <div class="value" id="stat-imar"></div>
        <div class="sub">Taşınmaz</div>
      </div>
    </div>

    <div class="charts-grid">
      <div class="chart-card">
        <h3>Niteliğe Göre Dağılım (Adet)</h3>
        <div class="chart-wrap"><canvas id="chartNitelik"></canvas></div>
      </div>
      <div class="chart-card">
        <h3>Niteliğe Göre Alan (m²)</h3>
        <div class="chart-wrap"><canvas id="chartAlan"></canvas></div>
      </div>
      <div class="chart-card">
        <h3>İlçe Bazında Taşınmaz Sayısı</h3>
        <div class="chart-wrap"><canvas id="chartIlce"></canvas></div>
      </div>
      <div class="chart-card">
        <h3>En Büyük 5 Taşınmaz (m²)</h3>
        <div class="chart-wrap"><canvas id="chartBuyuk"></canvas></div>
      </div>
    </div>
  </div>

  <!-- TAŞINMAZLAR SECTION -->
  <div id="sec-tasinmazlar" class="section">
    <div class="filter-bar">
      <input type="text" id="searchInput" placeholder="🔍  Taşınmaz ara (ada, parsel, mahalle, nitelik...)" oninput="filterCards()">
      <select id="filterNitelik" onchange="filterCards()">
        <option value="">Tüm Nitelikler</option>
      </select>
      <select id="filterIlce" onchange="filterCards()">
        <option value="">Tüm İlçeler</option>
      </select>
      <select id="filterAlert" onchange="filterCards()">
        <option value="">Tüm Durumlar</option>
        <option value="izale">İzale Davası Var</option>
        <option value="haciz">Haciz/Tedbir Var</option>
        <option value="imar">İmar Aykırı</option>
      </select>
      <span class="result-count" id="resultCount"></span>
    </div>
    <div class="prop-grid" id="propGrid"></div>
  </div>

  <!-- ANALİZ SECTION -->
  <div id="sec-analiz" class="section">
    <div class="analysis-section">
      <h2>📊 Genel Değerlendirme</h2>
      <div id="generalInsights"></div>
    </div>
    <div class="analysis-section">
      <h2>🔴 Risk & Hukuki Durum</h2>
      <div id="legalInsights"></div>
    </div>
    <div class="analysis-section">
      <h2>👥 En Çok Görünen Malikler (Çapraz Taşınmaz)</h2>
      <table class="owner-table" id="ownerTable">
        <thead><tr><th>#</th><th>Malik Adı</th><th>Taşınmaz Sayısı</th><th>Toplam Pay Alanı</th><th>Ortalama Pay</th><th>Örnek Parseller</th></tr></thead>
        <tbody id="ownerTableBody"></tbody>
      </table>
    </div>
    <div class="analysis-section">
      <h2>📐 Alan Dağılımı</h2>
      <div id="alanInsights"></div>
    </div>
  </div>

</div>

<!-- MODAL -->
<div class="modal-overlay" id="modalOverlay" onclick="closeModal(event)">
  <div class="modal" id="modalContent">
    <div class="modal-header">
      <h2 id="modalTitle">Taşınmaz Detayı</h2>
      <span class="modal-close" onclick="closeModalDirect()">✕</span>
    </div>
    <div class="modal-body" id="modalBody"></div>
  </div>
</div>

<footer>Tapu Analiz Sistemi &mdash; __DATE__ &mdash; __COUNT__ taşınmaz kaydı</footer>

<script>
const DATA = __DATA__;
const STATS = __STATS__;

// ── NAVIGATION ──
function showSection(name) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
  document.getElementById('sec-' + name).classList.add('active');
  event.target.classList.add('active');
}

// ── INIT ──
window.addEventListener('DOMContentLoaded', () => {
  renderSummaryCards();
  renderCharts();
  renderPropCards(DATA);
  populateFilters();
  renderAnalysis();
});

// ── SUMMARY CARDS ──
function renderSummaryCards() {
  document.getElementById('stat-toplam').textContent = STATS.toplam_tasinmaz;
  document.getElementById('stat-alan').textContent = parseFloat(STATS.toplam_alan_m2).toLocaleString('tr-TR', {maximumFractionDigits:0});
  document.getElementById('stat-tur').textContent = Object.keys(STATS.nitelik_dagilimi).length;
  document.getElementById('stat-izale').textContent = STATS.izale_sayisi;
  document.getElementById('stat-haciz').textContent = STATS.haciz_sayisi;
  document.getElementById('stat-imar').textContent = STATS.imar_aykiri_sayisi;
}

// ── CHARTS ──
const COLORS = ['#1e3a5f','#c0392b','#27ae60','#f39c12','#8e44ad','#16a085','#d35400','#2980b9','#c0392b','#7f8c8d'];

function renderCharts() {
  const nd = STATS.nitelik_dagilimi;
  const labels = Object.keys(nd);
  const adetVals = labels.map(k => nd[k].adet);
  const alanVals = labels.map(k => parseFloat(nd[k].toplam_m2.toFixed(0)));

  new Chart(document.getElementById('chartNitelik'), {
    type: 'doughnut',
    data: { labels, datasets: [{ data: adetVals, backgroundColor: COLORS, borderWidth: 2 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'right' } } }
  });

  new Chart(document.getElementById('chartAlan'), {
    type: 'bar',
    data: { labels, datasets: [{ label: 'm²', data: alanVals, backgroundColor: COLORS, borderRadius: 6 }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { y: { ticks: { callback: v => v.toLocaleString('tr-TR') } } }
    }
  });

  const ilce = STATS.ilce_dagilimi;
  const ilceLabels = Object.keys(ilce).slice(0, 10);
  const ilceVals = ilceLabels.map(k => ilce[k]);
  new Chart(document.getElementById('chartIlce'), {
    type: 'bar',
    data: { labels: ilceLabels, datasets: [{ data: ilceVals, backgroundColor: '#1e3a5f', borderRadius: 5 }] },
    options: {
      indexAxis: 'y',
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } }
    }
  });

  const top5 = STATS.en_buyuk_5;
  const t5Labels = top5.map(t => t.dosya.substring(0, 30) + '…');
  const t5Vals = top5.map(t => t.alan);
  new Chart(document.getElementById('chartBuyuk'), {
    type: 'bar',
    data: { labels: t5Labels, datasets: [{ data: t5Vals, backgroundColor: '#27ae60', borderRadius: 5 }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { y: { ticks: { callback: v => v.toLocaleString('tr-TR') } } }
    }
  });
}

// ── PROPERTY CARDS ──
function renderPropCards(list) {
  const grid = document.getElementById('propGrid');
  grid.innerHTML = '';
  document.getElementById('resultCount').textContent = list.length + ' taşınmaz';

  if (!list.length) {
    grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:40px;color:#999">Sonuç bulunamadı.</div>';
    return;
  }

  list.forEach((d, idx) => {
    const tags = [];
    if (d.izale_var) tags.push('<span class="tag izale">⚖️ İzale</span>');
    if (d.haciz_var) tags.push('<span class="tag haciz">🔒 Haciz</span>');
    if (d.imar_aykiri) tags.push('<span class="tag imar">🏗️ İmar Aykırı</span>');
    if (d.serh_sayisi > 0) tags.push(`<span class="tag serh">📌 ${d.serh_sayisi} Şerh</span>`);

    const nit = (d.nitelik_norm || '').toUpperCase();
    const alan = d.yuzolcum_sayi > 0 ? d.yuzolcum_sayi.toLocaleString('tr-TR') + ' m²' : '—';
    const title = d.nitelik || d.dosya.replace('.pdf','');
    const sub = [d.il_ilce, d.mahalle].filter(Boolean).join(' / ');

    const card = document.createElement('div');
    card.className = 'prop-card';
    card.setAttribute('data-idx', idx);
    card.innerHTML = `
      <div class="card-header">
        <span class="card-no">#${d.dosya_no || (idx+1)}</span>
        <span class="badge ${nit}">${d.nitelik_norm || 'Diğer'}</span>
      </div>
      <h4>${escHtml(d.mahalle || d.mevkii || title)}</h4>
      <div class="meta">📍 ${escHtml(sub) || d.il_ilce || '—'}</div>
      <div class="meta">🏷️ Ada/Parsel: ${escHtml(d.ada_parsel) || '—'}</div>
      <div class="area">${alan}</div>
      <div class="tags">${tags.join('')}</div>
    `;
    card.addEventListener('click', () => openModal(d));
    grid.appendChild(card);
  });
}

function escHtml(s) {
  if (!s) return '';
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── FILTERS ──
function populateFilters() {
  const nits = [...new Set(DATA.map(d => d.nitelik_norm).filter(Boolean))].sort();
  const nSel = document.getElementById('filterNitelik');
  nits.forEach(n => { const o = document.createElement('option'); o.value = n; o.textContent = n; nSel.appendChild(o); });

  const ilceler = [...new Set(DATA.map(d => d.il_ilce).filter(Boolean))].sort();
  const iSel = document.getElementById('filterIlce');
  ilceler.forEach(i => { const o = document.createElement('option'); o.value = i; o.textContent = i; iSel.appendChild(o); });
}

function filterCards() {
  const q = document.getElementById('searchInput').value.toLowerCase().trim();
  const nit = document.getElementById('filterNitelik').value;
  const ilce = document.getElementById('filterIlce').value;
  const alert = document.getElementById('filterAlert').value;

  const filtered = DATA.filter(d => {
    const haystack = [d.dosya, d.mahalle, d.mevkii, d.ada_parsel, d.il_ilce, d.nitelik, d.nitelik_norm, d.kimlik_no].join(' ').toLowerCase();
    if (q && !haystack.includes(q)) return false;
    if (nit && d.nitelik_norm !== nit) return false;
    if (ilce && d.il_ilce !== ilce) return false;
    if (alert === 'izale' && !d.izale_var) return false;
    if (alert === 'haciz' && !d.haciz_var) return false;
    if (alert === 'imar' && !d.imar_aykiri) return false;
    return true;
  });
  renderPropCards(filtered);
}

// ── MODAL ──
function openModal(d) {
  document.getElementById('modalTitle').textContent = (d.nitelik_norm || 'Taşınmaz') + ' — ' + (d.mahalle || d.mevkii || d.dosya);
  const body = document.getElementById('modalBody');

  const alerts = [];
  if (d.izale_var) alerts.push('<div class="alert-box">⚖️ Bu taşınmaz için <strong>İzale-i Şuyu (Ortaklığın Giderilmesi)</strong> davası açılmış durumda.</div>');
  if (d.haciz_var) alerts.push('<div class="alert-box">🔒 Bu taşınmazda <strong>haciz veya ihtiyati tedbir</strong> kaydı bulunmaktadır.</div>');
  if (d.imar_aykiri) alerts.push('<div class="alert-box">🏗️ Bu taşınmazda <strong>imar mevzuatına aykırı yapı</strong> kaydı bulunmaktadır.</div>');

  const owners = d.malikler && d.malikler.length > 0
    ? `
      <div class="insight" style="margin-bottom:12px;">
        Tespit edilen toplam pay: <strong>%${Number(d.hisse_toplam || 0).toLocaleString('tr-TR', {maximumFractionDigits: 4})}</strong>
        ${d.malikler[0] ? `&nbsp;|&nbsp; En büyük pay: <strong>${escHtml(d.malikler[0].ad)}</strong> (%${Number(d.malikler[0].yuzde_toplam || 0).toLocaleString('tr-TR', {maximumFractionDigits: 4})})` : ''}
      </div>
      <table class="owner-table">
        <thead><tr><th>Malik</th><th>Hisseler</th><th>Toplam %</th><th>Yaklaşık m²</th></tr></thead>
        <tbody>
          ${d.malikler.map(m => `
            <tr>
              <td><strong>${escHtml(m.ad)}</strong></td>
              <td>${(m.hisseler || [{hisse: m.hisse, yuzde: m.yuzde_toplam, m2: m.m2_toplam}]).map(h => `${escHtml(h.hisse)} <span style="color:#7f8c8d">(%${Number(h.yuzde || 0).toLocaleString('tr-TR', {maximumFractionDigits: 4})}${h.m2 ? ` | ${Number(h.m2).toLocaleString('tr-TR', {maximumFractionDigits: 2})} m²` : ''})</span>`).join('<br>')}</td>
              <td>%${Number(m.yuzde_toplam || 0).toLocaleString('tr-TR', {maximumFractionDigits: 4})}</td>
              <td>${Number(m.m2_toplam || 0).toLocaleString('tr-TR', {maximumFractionDigits: 2})} m²</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `
    : '<p style="color:#999;font-size:.85rem">Malik bilgisi çıkarılamadı.</p>';

  const serhHTML = d.serhler && d.serhler.length > 0
    ? d.serhler.map(s => `<div class="serh-item"><strong>${escHtml(s.tip)}:</strong> ${escHtml(s.aciklama)}</div>`).join('')
    : '<p style="color:#999;font-size:.85rem">Şerh/Beyan kaydı bulunamadı.</p>';

  body.innerHTML = `
    ${alerts.join('')}
    <div class="modal-section">
      <h3>Tapu Kayıt Bilgileri</h3>
      <div class="info-grid">
        <div class="info-row"><span class="key">Taşınmaz Kimlik No</span><span class="val">${escHtml(d.kimlik_no)||'—'}</span></div>
        <div class="info-row"><span class="key">Ada / Parsel</span><span class="val">${escHtml(d.ada_parsel)||'—'}</span></div>
        <div class="info-row"><span class="key">Nitelik</span><span class="val">${escHtml(d.nitelik)||'—'}</span></div>
        <div class="info-row"><span class="key">Yüzölçüm</span><span class="val">${d.yuzolcum_sayi > 0 ? d.yuzolcum_sayi.toLocaleString('tr-TR') + ' m²' : '—'}</span></div>
        <div class="info-row"><span class="key">İl / İlçe</span><span class="val">${escHtml(d.il_ilce)||'—'}</span></div>
        <div class="info-row"><span class="key">Mahalle / Köy</span><span class="val">${escHtml(d.mahalle)||'—'}</span></div>
        <div class="info-row"><span class="key">Mevkii</span><span class="val">${escHtml(d.mevkii)||'—'}</span></div>
        <div class="info-row"><span class="key">Cilt / Sayfa</span><span class="val">${escHtml(d.cilt_sayfa)||'—'}</span></div>
        <div class="info-row"><span class="key">Kayıt Durumu</span><span class="val">${escHtml(d.kayit_durum)||'—'}</span></div>
        <div class="info-row"><span class="key">Belge Tarihi</span><span class="val">${escHtml(d.belge_tarihi)||'—'}</span></div>
        <div class="info-row"><span class="key">Kurum</span><span class="val">${escHtml(d.kurum)||'—'}</span></div>
        <div class="info-row"><span class="key">Sayfa Sayısı</span><span class="val">${escHtml(String(d.sayfa_sayisi||'—'))}</span></div>
      </div>
    </div>
    <div class="modal-section">
      <h3>Malikler (${d.malik_sayisi || 0})</h3>
      ${owners}
    </div>
    <div class="modal-section">
      <h3>Şerh / Beyan / İrtifak (${d.serh_sayisi || 0})</h3>
      ${serhHTML}
    </div>
    ${d.hata ? `<div class="alert-box">⚠️ PDF Okuma Uyarısı: ${escHtml(d.hata)}</div>` : ''}
  `;

  document.getElementById('modalOverlay').classList.add('open');
}

function closeModal(e) {
  if (e.target === document.getElementById('modalOverlay')) closeModalDirect();
}
function closeModalDirect() {
  document.getElementById('modalOverlay').classList.remove('open');
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModalDirect(); });

// ── ANALYSIS ──
function renderAnalysis() {
  const total = STATS.toplam_tasinmaz;
  const alan = parseFloat(STATS.toplam_alan_m2);
  const nd = STATS.nitelik_dagilimi;

  // General
  const tarlaCount = nd['TARLA'] ? nd['TARLA'].adet : 0;
  const tarlaAlan = nd['TARLA'] ? nd['TARLA'].toplam_m2 : 0;
  const arsaCount = nd['ARSA'] ? nd['ARSA'].adet : 0;
  const gen = document.getElementById('generalInsights');
  gen.innerHTML = `
    <div class="insight success">
      Portföyde toplam <strong>${total} taşınmaz</strong> bulunmaktadır. Toplam alan <strong>${alan.toLocaleString('tr-TR', {maximumFractionDigits:0})} m²</strong> olup bu yaklaşık <strong>${(alan/10000).toFixed(2)} dönüm</strong> etmektedir.
    </div>
    <div class="insight">
      En yaygın taşınmaz türü <strong>Tarla</strong>'dır: ${tarlaCount} adet, toplam ${tarlaAlan.toLocaleString('tr-TR', {maximumFractionDigits:0})} m².
      ${arsaCount > 0 ? `<br>Arsa kategorisinde <strong>${arsaCount} taşınmaz</strong> bulunmaktadır.` : ''}
    </div>
    <div class="insight">
      Taşınmazların büyük çoğunluğu <strong>İstanbul</strong> ili sınırları içinde, çeşitli ilçe ve mahallelerde konumlanmaktadır. İki farklı ilçe grubu tespit edilmiştir: <strong>Bahçelievler</strong> ve <strong>Kapakılı (Tekirdağ)</strong> bölgeleri.
    </div>
  `;

  // Legal
  const legal = document.getElementById('legalInsights');
  const izP = ((STATS.izale_sayisi / total) * 100).toFixed(0);
  const hacP = ((STATS.haciz_sayisi / total) * 100).toFixed(0);
  const imarP = ((STATS.imar_aykiri_sayisi / total) * 100).toFixed(0);
  legal.innerHTML = `
    ${STATS.izale_sayisi > 0 ? `<div class="insight danger">⚖️ <strong>${STATS.izale_sayisi} taşınmazda (%${izP})</strong> İzale-i Şuyu (Ortaklığın Giderilmesi) davası tespit edilmiştir. Bu taşınmazlar mahkeme kararıyla satışa çıkarılabilir.</div>` : '<div class="insight success">✅ İzale-i Şuyu davası tespit edilmedi.</div>'}
    ${STATS.haciz_sayisi > 0 ? `<div class="insight warning">🔒 <strong>${STATS.haciz_sayisi} taşınmazda (%${hacP})</strong> haciz veya ihtiyati tedbir kaydı bulunmaktadır. Bu taşınmazlar üzerinde tasarruf kısıtlaması mevcuttur.</div>` : ''}
    ${STATS.imar_aykiri_sayisi > 0 ? `<div class="insight danger">🏗️ <strong>${STATS.imar_aykiri_sayisi} taşınmazda (%${imarP})</strong> imar mevzuatına aykırı yapı kaydı mevcuttur (3194 sayılı Kanun 32. Madde kapsamında).</div>` : ''}
    <div class="insight">📌 Toplam şerh/beyan yoğunluğu: Ortalama her taşınmazda ${(DATA.reduce((a,b) => a + (b.serh_sayisi||0), 0) / total).toFixed(1)} adet şerh/beyan kaydı bulunmaktadır.</div>
  `;

  // Owner table
  const tbody = document.getElementById('ownerTableBody');
  tbody.innerHTML = '';
  const ownerSummary = STATS.malik_ozet || [];
  if (!ownerSummary.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#999;padding:16px">Malik özeti çıkarılamadı.</td></tr>';
  } else {
    const maxCount = ownerSummary[0].tasinmaz_sayisi || 1;
    ownerSummary.forEach((owner, i) => {
      const pct = ((owner.tasinmaz_sayisi / total) * 100).toFixed(1);
      tbody.innerHTML += `<tr>
        <td>${i+1}</td>
        <td><strong>${escHtml(owner.ad)}</strong></td>
        <td>
          <div><strong>${owner.tasinmaz_sayisi}</strong></div>
          <div class="progress-bar"><div class="fill" style="width:${Math.min(owner.tasinmaz_sayisi / maxCount * 100, 100)}%"></div></div>
        </td>
        <td>${Number(owner.toplam_m2 || 0).toLocaleString('tr-TR', {maximumFractionDigits: 2})} m²</td>
        <td>%${Number(owner.ortalama_yuzde || 0).toLocaleString('tr-TR', {maximumFractionDigits: 4})}<div style="font-size:.78rem;color:#7f8c8d;">Portföy görünümü %${pct}</div></td>
        <td>${(owner.ornekler || []).map(escHtml).join(', ') || '—'}</td>
      </tr>`;
    });
  }

  // Area insights
  const sorted = [...DATA].sort((a,b) => b.yuzolcum_sayi - a.yuzolcum_sayi);
  const top3 = sorted.slice(0,3);
  const small3 = sorted.slice(-3).reverse();
  const avg = alan / total;
  const alanEl = document.getElementById('alanInsights');
  alanEl.innerHTML = `
    <div class="insight">
      Ortalama taşınmaz büyüklüğü: <strong>${avg.toLocaleString('tr-TR', {maximumFractionDigits:0})} m²</strong>
    </div>
    <div class="insight success">
      <strong>En büyük 3 taşınmaz:</strong><br>
      ${top3.map((d,i) => `${i+1}. ${escHtml(d.mahalle||d.mevkii||d.dosya)} — <strong>${d.yuzolcum_sayi.toLocaleString('tr-TR')} m²</strong> (${escHtml(d.nitelik_norm||'?')})`).join('<br>')}
    </div>
    <div class="insight">
      <strong>En küçük 3 taşınmaz:</strong><br>
      ${small3.map((d,i) => `${i+1}. ${escHtml(d.mahalle||d.mevkii||d.dosya)} — <strong>${d.yuzolcum_sayi.toLocaleString('tr-TR')} m²</strong> (${escHtml(d.nitelik_norm||'?')})`).join('<br>')}
    </div>
  `;
}
</script>
</body>
</html>
"""

def generate_html(data, stats):
    # Clean data for JSON (remove huge tam_metin to keep file small)
    for d in data:
        d.pop("tam_metin", None)

    data_json = json.dumps(data, ensure_ascii=False, indent=None)
    stats_json = json.dumps(stats, ensure_ascii=False, indent=None)

    from datetime import datetime
    date_str = datetime.now().strftime("%d.%m.%Y %H:%M")

    html = HTML_TEMPLATE
    html = html.replace("__DATA__", data_json)
    html = html.replace("__STATS__", stats_json)
    html = html.replace("__DATE__", date_str)
    html = html.replace("__COUNT__", str(len(data)))
    return html


# ─────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  TAPU ANALİZ ARACI")
    print("=" * 60)

    data = load_all_pdfs()
    print(f"\n[INFO] {len(data)} PDF okundu. İstatistikler hesaplanıyor...")

    stats = compute_stats(data)

    print(f"[INFO] Toplam alan: {stats['toplam_alan_m2_fmt']} m²")
    print(f"[INFO] İzale davası: {stats['izale_sayisi']}")
    print(f"[INFO] HTML raporu oluşturuluyor...")

    html = generate_html(data, stats)

    os.makedirs(os.path.dirname(OUTPUT_HTML), exist_ok=True)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n[OK] Rapor oluşturuldu: {OUTPUT_HTML}")
    print(f"[OK] Tarayıcınızda açmak için dosyayı çift tıklayın.")

    # Auto-open in browser
    import webbrowser
    webbrowser.open(f"file:///{OUTPUT_HTML.replace(chr(92), '/')}")
    print("[OK] Tarayıcıda aciliyor...")
