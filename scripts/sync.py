#!/usr/bin/env python3
"""
Sincroniza el registro de personas del sismo desde una carpeta pública de Google Drive
y genera docs/data.json (formato compacto) para el buscador.

Fuentes:
  - Google Docs "REGISTRO MAESTRO"            (N° | Hospital | Nombre | Edad)
  - PDFs consolidados                          (NUM | Apellidos Nombres | CI | Edad | Sexo | Proc | Hospital)
  - Reporte HUC (Universitario)                (Fallecidos / Heridos con ESTADO)
  - Transcripciones manuales de imágenes       (data/manual/*.json) — fotos de listas por hospital/refugio

La cédula (CI) se usa solo para deduplicar; NUNCA se publica.
Salida compacta: hospitales por índice + filas como arrays, para minimizar bytes (baja conexión).

Uso:  python3 scripts/sync.py     ·     Dep:  pypdf
"""
import json, re, hashlib, sys, io, urllib.request, unicodedata
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "docs"
RAW = ROOT / "data" / "raw"
MANUAL = ROOT / "data" / "manual"
FOLDER_ID = "1o36ifaRz45kAs5rKzci49aD0mP5JB_YI"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"

HOSPITAL_RULES = [
    ("UNIVERSITARIO",     "Hospital Universitario de Caracas"),
    ("HUC",               "Hospital Universitario de Caracas"),
    ("PEREZ CARRE",       "Hospital Pérez Carreño"),
    ("LUCIANI",           "Hospital Domingo Luciani (El Llanito)"),
    ("VARGAS DE CARACAS", "Hospital Vargas de Caracas"),
    ("VARGAS LA GUAIRA",  "Hospital José María Vargas (La Guaira)"),
    ("JOSE MARIA VARGAS", "Hospital José María Vargas (La Guaira)"),
    ("DE LOS RIOS",       "Hospital J. M. de los Ríos (pediátrico)"),
    ("BAQUERO",           "Hospital Ricardo Baquero González"),
    ("CATIA",             "Hospital de Catia (J. G. Hernández)"),
    ("JOSE GREGORIO",     "Hospital de Catia (J. G. Hernández)"),
    ("MILITAR",           "Hospital Militar Carlos Arvelo"),
    ("ARVELO",            "Hospital Militar Carlos Arvelo"),
    ("VARGAS",            "Hospital Vargas de Caracas"),
]


def fetch(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = r.read()
    return data if binary else data.decode("utf-8", "replace")


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn")


def canon_hospital(raw):
    key = strip_accents(raw).upper()
    for needle, canon in HOSPITAL_RULES:
        if needle in key:
            return canon
    return None


def list_drive_folder(folder_id):
    html = fetch(f"https://drive.google.com/drive/folders/{folder_id}")
    items, seen = [], set()
    pat = re.compile(r"aria-label=\"((?:[^\"\\]|\\.)*?)\"[^>]*?ssk='[^:]*:[^:]*:([A-Za-z0-9_-]{25,60})")
    aux = re.compile(r"^(Modified|Size|More actions|Storage|Lista de)", re.I)
    for m in pat.finditer(html):
        label, raw_id = m.group(1), m.group(2)
        fid = re.sub(r"-0(-\d+)?$", "", raw_id)
        low = label.lower()
        typ = ("folder" if "folder" in low else "pdf" if "pdf" in low
               else "gdoc" if "google docs" in low else "image" if "image" in low else None)
        name = re.sub(r"\s+(Shared folder|PDF Shared|Google Docs Shared|Image Shared|Shared).*$", "", label).strip()
        if not typ or fid in seen or aux.match(name):
            continue
        seen.add(fid)
        items.append({"id": fid, "name": name, "type": typ})
    return items


def export_gdoc_text(doc_id):
    return fetch(f"https://docs.google.com/document/d/{doc_id}/export?format=txt")


def download_pdf_text(file_id):
    raw = fetch(f"https://drive.google.com/uc?export=download&id={file_id}", binary=True)
    if not raw.startswith(b"%PDF"):
        return None
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(raw))
    return "\n".join((p.extract_text() or "") for p in reader.pages)


# ---------- localidades que a veces se pegan al nombre ----------
LOCATIONS = ["LA GUAIRA", "CATIA LA MAR", "CARABALLEDA", "MAIQUETIA", "MAIQUETÍA", "NAIGUATA",
             "NAIGUATÁ", "MACUTO", "CARMEN DE URIA", "LA SABANA", "TARMA", "OSMA", "TODASANA",
             "CHICHIRIVICHE", "PETARE", "CATIA", "EL CARIBE", "CARIBE", "COCALES", "GUAIRA",
             "EL JUNQUITO", "ANTIMANO", "ANTÍMANO", "CARAPITA", "MACARAO", "SABANA GRANDE",
             "BARUTA", "EL VALLE", "CARACAS"]
_LOC_RE = re.compile(r"\s+(?:" + "|".join(re.escape(l) for l in LOCATIONS) + r")\s*$", re.I)


def split_trailing_location(name):
    loc = None
    while True:
        m = _LOC_RE.search(name)
        if not m:
            break
        seg = name[m.start():].strip()
        loc = seg if loc is None else seg + " " + loc
        name = name[:m.start()].strip()
    return name, loc


def clean_name(name):
    name = re.sub(r"\s+", " ", name).strip(" .)-")
    toks, out = name.split(), []
    for t in toks:
        if not out or out[-1] != t:
            out.append(t)
    if len(out) >= 2 and len(out) % 2 == 0 and out[: len(out)//2] == out[len(out)//2:]:
        out = out[: len(out)//2]
    return " ".join(out)


# ---------- parser Google Doc maestro ----------
NAME_RE = re.compile(r"^[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ .'\-/0-9]{2,}$")
HDR_RE = re.compile(r"^(\d{1,4})\s+(Hospital.+)$")


def parse_master_doc(text, source):
    cells = [c.strip() for c in text.replace("\n", "\t").split("\t") if c.strip()]
    records, cur = [], None

    def flush():
        if cur and cur.get("name") and canon_hospital(cur["hospital"]):
            records.append({"name": clean_name(cur["name"]), "age": cur["age"],
                            "hospital": canon_hospital(cur["hospital"]),
                            "sex": None, "origin": None, "estado": None, "ci": None, "source": source})
    i = 0
    while i < len(cells):
        c = cells[i]
        m = HDR_RE.match(c)
        lone = c.isdigit() and i + 1 < len(cells) and cells[i + 1].lower().startswith("hospital")
        if m or lone:
            flush()
            cur = {"hospital": m.group(2) if m else cells[i + 1], "name": None, "age": None}
            i += 1 if m else 2
            continue
        if cur is not None:
            if cur["name"] is None and NAME_RE.match(c) and not c.lower().startswith("hospital"):
                cur["name"] = c
            elif cur["name"] and cur["age"] is None and re.fullmatch(r"\d{1,3}", c) and 0 < int(c) < 120:
                cur["age"] = int(c)
        i += 1
    flush()
    return records


# ---------- parser PDFs consolidados (HOSPITAL por línea) ----------
HOSP_IN_LINE = re.compile(r"(HO?SPITAL\b.*)", re.I)


def parse_consolidated_pdf(text, source):
    records = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("NUM ") or "ACTUALIZACION" in line or line.startswith("N° "):
            continue
        m = HOSP_IN_LINE.search(line)
        if not m:
            continue
        hosp = canon_hospital(m.group(1))
        if not hosp:
            continue
        pre = re.sub(r"^\d+\s+", "", line[: m.start()])
        name_toks = []
        for tok in pre.split():
            if any(ch.isdigit() for ch in tok) or (tok in ("M", "F") and name_toks):
                break
            name_toks.append(tok)
        name = clean_name(" ".join(name_toks))
        name, loc = split_trailing_location(name)
        if len(name) < 4 or " " not in name:
            continue
        rest = pre[len(" ".join(name_toks)):]
        ci = next((n for n in re.findall(r"\d{6,9}", rest)), None)
        age = next((int(n) for n in re.findall(r"\b(\d{1,3})\b", rest) if 0 < int(n) <= 110), None)
        sex = "F" if re.search(r"\bF\b", rest) else "M" if re.search(r"\bM\b", rest) else None
        origin = re.sub(r"[\d)]+", " ", rest)
        origin = re.sub(r"\b[MF]\b", " ", origin)
        origin = re.sub(r"\s+", " ", origin).strip(" .,-") or loc or None
        records.append({"name": name, "age": age, "hospital": hosp, "sex": sex,
                        "origin": origin, "estado": None, "ci": ci, "source": source})
    return records


# ---------- parser Reporte HUC (Fallecidos / Heridos) ----------
def parse_huc_report(text, source):
    records = []
    section = None  # "fallecido" | "herido"
    hosp = "Hospital Universitario de Caracas"
    DIAG = re.compile(r"(POLITRAUMA\w*|TRAUMATISMO[^,\n]*|FRACTURA[^,\n]*|FALLECI\w*)", re.I)
    for line in text.split("\n"):
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        up = strip_accents(line).upper()
        if "CUADRO DE FALLECIDOS" in up:
            section = "fallecido"; continue
        if "CUADRO DE HERIDOS" in up or "HERIDOS Y LESIONADOS" in up:
            section = "herido"; continue
        if section is None or up.startswith("N NOMBRE") or "CEDULA" in up or "IDENTIDAD" in up:
            continue
        m = re.match(r"^(\d{1,3})\s+([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ .'-]+?)\s+(\d{1,3})\s*(?:AÑOS?)?\b(.*)$", line)
        if not m:
            continue
        name, age, rest = m.group(2).strip(), int(m.group(3)), m.group(4)
        if not (0 < age <= 110) or " " not in name.strip():
            continue
        ci = next((n for n in re.findall(r"\d{6,9}", rest)), None)
        diag = DIAG.search(rest)
        if section == "fallecido":
            estado = "Fallecido"
        else:
            estado = diag.group(1).title() if diag else "Herido / lesionado"
        origin = re.sub(r"\d{6,9}", " ", rest)
        if diag:
            origin = origin.replace(diag.group(0), " ")
        origin = re.sub(r"\b(ALTA MEDICA|NO ESPECIFICA)\b", " ", origin, flags=re.I)
        origin = re.sub(r"[()/]", " ", origin)
        origin = re.sub(r"\s+", " ", origin).strip(" .,-") or None
        records.append({"name": clean_name(name), "age": age, "hospital": hosp, "sex": None,
                        "origin": origin, "estado": estado, "ci": ci, "source": source})
    return records


# ---------- carga de transcripciones manuales (imágenes) ----------
def load_manual():
    records = []
    if not MANUAL.exists():
        return records, []
    files = []
    for f in sorted(MANUAL.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        rows = data.get("patients", data) if isinstance(data, dict) else data
        src = data.get("source", f.stem) if isinstance(data, dict) else f.stem
        n = 0
        for r in rows:
            hosp = r.get("hospital")
            if not r.get("name") or not hosp:
                continue
            records.append({"name": clean_name(r["name"]), "age": r.get("age"), "hospital": hosp,
                            "sex": r.get("sex"), "origin": r.get("origin"), "estado": r.get("estado"),
                            "ci": r.get("ci"), "source": src})
            n += 1
        files.append({"name": src, "records": n, "type": "imagen"})
    return records, files


# ---------- merge + dedup ----------
def merge(records):
    out = {}
    for r in records:
        if not r["name"] or len(r["name"]) < 4:
            continue
        ci = (r.get("ci") or "").strip()
        key = ci if ci else strip_accents(r["name"]).lower() + "|" + strip_accents(r["hospital"]).lower()
        rid = hashlib.sha1(key.encode()).hexdigest()[:10]
        cur = out.get(rid)
        if cur is None:
            out[rid] = {k: r.get(k) for k in ("name", "age", "hospital", "sex", "origin", "estado")}
        else:
            for f in ("age", "sex", "origin", "estado"):
                if not cur.get(f) and r.get(f):
                    cur[f] = r.get(f)
    return list(out.values())


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    WEB.mkdir(parents=True, exist_ok=True)

    print("Listando carpeta de Drive…", file=sys.stderr)
    root = list_drive_folder(FOLDER_ID)
    # recurre a subcarpetas para encontrar PDFs/Docs (p.ej. Reporte HUC)
    docs = [it for it in root if it["type"] in ("pdf", "gdoc")]
    for f in [it for it in root if it["type"] == "folder"]:
        try:
            for sub in list_drive_folder(f["id"]):
                if sub["type"] in ("pdf", "gdoc"):
                    docs.append(sub)
        except Exception as e:
            print(f"  ! subcarpeta {f['name']}: {e}", file=sys.stderr)

    all_records, sources = [], []
    seen_doc = set()
    for d in docs:
        if d["id"] in seen_doc:
            continue
        seen_doc.add(d["id"])
        up = d["name"].upper()
        try:
            if d["type"] == "gdoc":
                if "LINK" in up:
                    continue
                txt = export_gdoc_text(d["id"]); recs = parse_master_doc(txt, d["name"]); kind = "gdoc"
            else:
                txt = download_pdf_text(d["id"])
                if not txt or len(txt.strip()) < 80:
                    print(f"  - {d['name'][:46]}: sin texto (imagen) — omitido", file=sys.stderr); continue
                if "HUC" in up or "FALLECIDOS" in txt.upper()[:4000] or "REPORTE DE PERSONAS" in txt.upper()[:2000]:
                    recs = parse_huc_report(txt, d["name"]); kind = "pdf-huc"
                else:
                    recs = parse_consolidated_pdf(txt, d["name"]); kind = "pdf"
        except Exception as e:
            print(f"  ! {d['name'][:46]}: {e}", file=sys.stderr); continue
        h = hashlib.sha1(txt.encode("utf-8", "replace")).hexdigest()
        (RAW / f"{d['id']}.txt").write_text(txt, encoding="utf-8")
        print(f"  {kind:8} {d['name'][:46]:46} -> {len(recs):4} reg", file=sys.stderr)
        all_records += recs
        sources.append({"name": d["name"], "type": kind, "records": len(recs)})

    man_recs, man_files = load_manual()
    if man_recs:
        print(f"  imágenes  transcripciones manuales{'':18} -> {len(man_recs):4} reg", file=sys.stderr)
    all_records += man_recs
    sources += man_files

    patients = merge(all_records)
    patients.sort(key=lambda p: strip_accents(p["name"]))

    # índice de hospitales para formato compacto
    hosp_list = sorted({p["hospital"] for p in patients})
    hidx = {h: i for i, h in enumerate(hosp_list)}
    by_hosp = {h: 0 for h in hosp_list}
    rows = []
    for p in patients:
        by_hosp[p["hospital"]] += 1
        # fila compacta: [nombre, idxHospital, edad|0, sexo|"", procedencia|"", estado|""]
        rows.append([p["name"], hidx[p["hospital"]], p["age"] or 0,
                     p["sex"] or "", p["origin"] or "", p["estado"] or ""])

    out = {
        "v": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "folder_id": FOLDER_ID,
        "total": len(rows),
        "hospitals": hosp_list,
        "by_hospital": [by_hosp[h] for h in hosp_list],
        "sources": sources,
        "rows": rows,
    }

    prev_file = WEB / "data.json"
    if prev_file.exists():
        try:
            prev = json.loads(prev_file.read_text(encoding="utf-8"))
            pn = {r[0] + str(r[1]) for r in prev.get("rows", [])}
            nn = {r[0] + str(r[1]) for r in rows}
            print(f"  cambios: +{len(nn - pn)} nuevos, -{len(pn - nn)} retirados", file=sys.stderr)
        except Exception:
            pass

    # JSON compacto (sin indentación, separadores sin espacios)
    prev_file.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    kb = prev_file.stat().st_size / 1024

    # sitemap con lastmod fresco (SEO)
    today = out["generated_at"][:10]
    (WEB / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        '  <url><loc>https://alealvb.github.io/sismo-vzla-pacientes/</loc>'
        f'<lastmod>{today}</lastmod><changefreq>hourly</changefreq><priority>1.0</priority></url>\n'
        '</urlset>\n', encoding="utf-8")
    print(f"OK: {len(rows)} personas, {len(hosp_list)} hospitales, {kb:.0f} KB -> {prev_file}", file=sys.stderr)
    for h in hosp_list:
        print(f"     {by_hosp[h]:4}  {h}", file=sys.stderr)


if __name__ == "__main__":
    main()
