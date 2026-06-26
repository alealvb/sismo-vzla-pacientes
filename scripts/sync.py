#!/usr/bin/env python3
"""
Sincroniza el registro de pacientes del sismo desde una carpeta pública de Google Drive
y genera web/data.json para el buscador.

Fuentes:
  - Google Docs "REGISTRO MAESTRO" (tabla N° | Hospital | Nombre | Edad)
  - PDFs consolidados (NUM | Apellidos Nombres | CI | Edad | Sexo | Procedencia | Hospital | Fecha)

La cédula (CI) se extrae para deduplicar pero NUNCA se publica (dato sensible).
Detecta cambios por hash del contenido de cada documento y reporta altas/bajas.

Uso:   python3 scripts/sync.py
Dep.:  pypdf   (pip install -r requirements.txt)
"""
import json, re, hashlib, sys, urllib.request, unicodedata
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
RAW = ROOT / "data" / "raw"
FOLDER_ID = "1o36ifaRz45kAs5rKzci49aD0mP5JB_YI"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"

# (substring sin acentos en MAYÚSCULAS, orden importa)  ->  nombre canónico para mostrar
HOSPITAL_RULES = [
    ("UNIVERSITARIO",        "Hospital Universitario de Caracas"),
    ("PEREZ CARRE",          "Hospital Pérez Carreño"),
    ("LUCIANI",              "Hospital Domingo Luciani (El Llanito)"),
    ("VARGAS DE CARACAS",    "Hospital Vargas de Caracas"),
    ("VARGAS LA GUAIRA",     "Hospital José María Vargas (La Guaira)"),
    ("JOSE MARIA VARGAS",    "Hospital José María Vargas (La Guaira)"),
    ("DE LOS RIOS",          "Hospital J. M. de los Ríos (pediátrico)"),
    ("BAQUERO",              "Hospital Ricardo Baquero González"),
    ("CATIA",                "Hospital de Catia (J. G. Hernández)"),
    ("JOSE GREGORIO",        "Hospital de Catia (J. G. Hernández)"),
    ("MILITAR",              "Hospital Militar Carlos Arvelo"),
    ("ARVELO",               "Hospital Militar Carlos Arvelo"),
    ("VARGAS",               "Hospital Vargas de Caracas"),
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
    """[{id,name,type}] de los items de una carpeta pública."""
    html = fetch(f"https://drive.google.com/drive/folders/{folder_id}")
    items, seen = [], set()
    pat = re.compile(r"aria-label=\"((?:[^\"\\]|\\.)*?)\"[^>]*?ssk='[^:]*:[^:]*:([A-Za-z0-9_-]{25,60})")
    for m in pat.finditer(html):
        label, raw_id = m.group(1), m.group(2)
        fid = re.sub(r"-0(-\d+)?$", "", raw_id)
        low = label.lower()
        typ = ("folder" if "folder" in low else "pdf" if "pdf" in low
               else "gdoc" if "google docs" in low else None)
        if not typ or fid in seen:
            continue
        seen.add(fid)
        name = re.sub(r"\s+(Shared folder|PDF Shared|Google Docs Shared|Shared).*$", "", label).strip()
        items.append({"id": fid, "name": name, "type": typ})
    return items


def export_gdoc_text(doc_id):
    return fetch(f"https://docs.google.com/document/d/{doc_id}/export?format=txt")


def download_pdf_text(file_id):
    raw = fetch(f"https://drive.google.com/uc?export=download&id={file_id}", binary=True)
    if not raw.startswith(b"%PDF"):
        return None, raw  # no es PDF (p.ej. página de confirmación)
    import io, pypdf
    reader = pypdf.PdfReader(io.BytesIO(raw))
    txt = "\n".join((p.extract_text() or "") for p in reader.pages)
    return txt, raw


# Localidades frecuentes (estado Vargas/La Guaira y Caracas) que a veces se pegan al nombre.
LOCATIONS = [
    "LA GUAIRA", "CATIA LA MAR", "CARABALLEDA", "MAIQUETIA", "MAIQUETÍA", "NAIGUATA",
    "NAIGUATÁ", "MACUTO", "CARMEN DE URIA", "LA SABANA", "TARMA", "OSMA", "TODASANA",
    "CHICHIRIVICHE", "PETARE", "CATIA", "EL CARIBE", "CARIBE", "COCALES", "GUAIRA",
    "EL JUNQUITO", "ANTIMANO", "ANTÍMANO", "CARAPITA", "MACARAO", "SABANA GRANDE",
    "BARUTA", "EL VALLE", "CARACAS",
]
_LOC_RE = re.compile(r"\s+(?:" + "|".join(re.escape(l) for l in LOCATIONS) + r")\s*$", re.I)


def split_trailing_location(name):
    """Si el nombre termina en una localidad conocida, la separa como procedencia."""
    loc = None
    while True:
        m = _LOC_RE.search(name)
        if not m:
            break
        loc = name[m.start():].strip() if loc is None else name[m.start():].strip() + " " + loc
        name = name[:m.start()].strip()
    return name, loc


# ---------- limpieza de nombres ----------
def clean_name(name):
    name = re.sub(r"\s+", " ", name).strip(" .)-")
    toks = name.split()
    out = []                                   # colapsa tokens repetidos consecutivos
    for t in toks:
        if not out or out[-1] != t:
            out.append(t)
    if len(out) >= 2 and len(out) % 2 == 0 and out[: len(out)//2] == out[len(out)//2:]:
        out = out[: len(out)//2]               # "WU SUSUP WU SUSUP" -> "WU SUSUP"
    return " ".join(out)


# ---------- parser del Google Doc maestro ----------
NAME_RE = re.compile(r"^[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ .'\-/0-9]{2,}$")
HDR_RE = re.compile(r"^(\d{1,4})\s+(Hospital.+)$")


def parse_master_doc(text, source):
    cells = [c.strip() for c in text.replace("\n", "\t").split("\t") if c.strip()]
    records, cur = [], None

    def flush():
        if cur and cur.get("name") and canon_hospital(cur["hospital"]):
            records.append({"name": clean_name(cur["name"]), "age": cur["age"],
                            "hospital": canon_hospital(cur["hospital"]),
                            "sex": None, "origin": None, "ci": None, "source": source})
    i = 0
    while i < len(cells):
        c = cells[i]
        m = HDR_RE.match(c)
        lone = c.isdigit() and i + 1 < len(cells) and cells[i + 1].lower().startswith("hospital")
        if m or lone:
            flush()
            if m:
                cur = {"hospital": m.group(2), "name": None, "age": None}; i += 1
            else:
                cur = {"hospital": cells[i + 1], "name": None, "age": None}; i += 2
            continue
        if cur is not None:
            if cur["name"] is None and NAME_RE.match(c) and not c.lower().startswith("hospital"):
                cur["name"] = c
            elif cur["name"] and cur["age"] is None and re.fullmatch(r"\d{1,3}", c) and 0 < int(c) < 120:
                cur["age"] = int(c)
        i += 1
    flush()
    return records


# ---------- parser de PDFs consolidados ----------
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
        pre = re.sub(r"^\d+\s+", "", line[: m.start()])     # quita NUM inicial
        name_toks = []
        for tok in pre.split():
            if any(ch.isdigit() for ch in tok):
                break
            if tok in ("M", "F") and name_toks:
                break
            name_toks.append(tok)
        name = clean_name(" ".join(name_toks))
        name, loc_in_name = split_trailing_location(name)
        if len(name) < 4 or " " not in name:
            continue
        rest = pre[len(" ".join(name_toks)):]
        ci = next((n for n in re.findall(r"\d{6,9}", rest)), None)
        age = next((int(n) for n in re.findall(r"\b(\d{1,3})\b", rest) if 0 < int(n) <= 110), None)
        sex = "F" if re.search(r"\bF\b", rest) else "M" if re.search(r"\bM\b", rest) else None
        origin = re.sub(r"[\d)]+", " ", rest)
        origin = re.sub(r"\b[MF]\b", " ", origin)
        origin = re.sub(r"\s+", " ", origin).strip(" .,-") or loc_in_name or None
        records.append({"name": name, "age": age, "hospital": hosp,
                        "sex": sex, "origin": origin, "ci": ci, "source": source})
    return records


# ---------- merge + dedup ----------
def merge(records):
    out = {}
    for r in records:
        if not r["name"] or len(r["name"]) < 4:
            continue
        ci = (r.get("ci") or "").strip()
        key = ci if ci else strip_accents(r["name"]).lower() + "|" + strip_accents(r["hospital"]).lower()
        rid = hashlib.sha1(key.encode()).hexdigest()[:12]
        cur = out.get(rid)
        if cur is None:
            out[rid] = {"id": rid, "name": r["name"], "age": r["age"], "hospital": r["hospital"],
                        "sex": r.get("sex"), "origin": r.get("origin"),
                        "sources": [r["source"]]}
        else:
            for f in ("age", "sex", "origin"):           # completa campos faltantes
                if not cur.get(f) and r.get(f):
                    cur[f] = r.get(f)
            if r["source"] not in cur["sources"]:
                cur["sources"].append(r["source"])
    return list(out.values())


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    WEB.mkdir(parents=True, exist_ok=True)

    print("Listando carpeta de Drive…", file=sys.stderr)
    items = list_drive_folder(FOLDER_ID)
    all_records, sources = [], []

    for d in items:
        if d["type"] == "gdoc" and "LINK" not in d["name"].upper():
            try:
                txt = export_gdoc_text(d["id"])
            except Exception as e:
                print(f"  ! gdoc {d['name']}: {e}", file=sys.stderr); continue
            recs = parse_master_doc(txt, d["name"])
            kind = "gdoc"
        elif d["type"] == "pdf":
            try:
                txt, _ = download_pdf_text(d["id"])
            except Exception as e:
                print(f"  ! pdf {d['name']}: {e}", file=sys.stderr); continue
            if not txt or len(txt.strip()) < 80:
                print(f"  - {d['name']}: sin texto (escaneado, requiere OCR) — omitido", file=sys.stderr)
                continue
            recs = parse_consolidated_pdf(txt, d["name"])
            kind = "pdf"
        else:
            continue

        h = hashlib.sha1(txt.encode("utf-8", "replace")).hexdigest()
        (RAW / f"{d['id']}.txt").write_text(txt, encoding="utf-8")
        print(f"  {kind:4} {d['name'][:48]:48} -> {len(recs):4} reg (hash {h[:8]})", file=sys.stderr)
        all_records += recs
        sources.append({"name": d["name"], "id": d["id"], "type": kind, "hash": h, "records": len(recs)})

    patients = merge(all_records)
    patients.sort(key=lambda p: strip_accents(p["name"]))

    by_hosp = {}
    for p in patients:
        by_hosp[p["hospital"]] = by_hosp.get(p["hospital"], 0) + 1

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "folder_id": FOLDER_ID,
        "total": len(patients),
        "by_hospital": dict(sorted(by_hosp.items(), key=lambda x: -x[1])),
        "sources": sources,
        "patients": patients,
    }

    prev_file = WEB / "data.json"
    if prev_file.exists():
        try:
            prev = json.loads(prev_file.read_text(encoding="utf-8"))
            pi = {p["id"] for p in prev.get("patients", [])}
            ni = {p["id"] for p in patients}
            print(f"  cambios: +{len(ni - pi)} nuevos, -{len(pi - ni)} retirados", file=sys.stderr)
        except Exception:
            pass

    prev_file.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK: {len(patients)} pacientes ({len(by_hosp)} hospitales) -> {prev_file}", file=sys.stderr)
    print(json.dumps(by_hosp, ensure_ascii=False, indent=1), file=sys.stderr)


if __name__ == "__main__":
    main()
