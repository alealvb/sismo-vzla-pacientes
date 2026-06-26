#!/usr/bin/env python3
"""
OCR local de las fotos de listas (manuscritas e impresas) con un modelo de visión
(Qwen2.5-VL vía Ollama, en GPU). Recorre las subcarpetas de la carpeta de Drive,
transcribe cada imagen a registros estructurados y escribe data/manual/ocr-images.json
para que sync.py lo incorpore.

Caché por hash de contenido en data/ocr-cache/<hash>.json: cada imagen se procesa
una sola vez; en corridas posteriores solo se OCR-ean imágenes nuevas o modificadas.

Requisitos:  Ollama corriendo (ollama serve) con el modelo qwen2.5vl:7b, y Pillow.
Uso:  python3 scripts/ocr_images.py
"""
import base64, io, json, hashlib, sys, urllib.request
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync import list_drive_folder, fetch, FOLDER_ID  # reutiliza utilidades

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "ocr-cache"
OUT = ROOT / "data" / "manual" / "ocr-images.json"
OLLAMA = "http://localhost:11434/api/generate"
MODEL = "qwen2.5vl:7b"

# subcarpeta (substring en mayúsculas, sin acentos) -> destino canónico
FOLDER_DEST = [
    ("CARLOS ARVELO",  "Hospital Militar Carlos Arvelo"),
    ("ARVELEDO",       "Hospital Militar Carlos Arvelo"),
    ("CATIA",          "Hospital Ricardo Baquero González"),   # Periférico de Catia
    ("VARGAS",         "Hospital Vargas de Caracas"),
    ("LUCIANI",        "Hospital Domingo Luciani (El Llanito)"),
    ("PEREZ CARRE",    "Hospital Pérez Carreño"),
    ("UNIVERSITARIO",  "Hospital Universitario de Caracas"),
    ("GOLF",           "Refugio: Campo de Golf Playa Los Cocos"),
    ("COCOS",          "Refugio: Campo de Golf Playa Los Cocos"),
]

PROMPT = (
    "Esta es la foto de una lista de personas (pacientes de hospital o personas en un refugio) "
    "tras el sismo en Venezuela. Transcribe TODAS las filas/nombres que veas. "
    'Responde SOLO con JSON: {"people":[{"name":"Nombre y Apellido","age":entero|null,'
    '"ci":"cédula"|null,"origin":"procedencia/dirección"|null}]}. '
    "Transcribe el manuscrito lo mejor posible. No inventes datos: si un campo no está, usa null. "
    "Incluye menores y personas sin edad/cédula."
)


def strip(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def dest_for(folder_name):
    key = strip(folder_name).upper()
    for needle, dest in FOLDER_DEST:
        if needle in key:
            return dest
    return None


def prep_image(raw, maxside=1600):
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = im.size
    if max(w, h) > maxside:
        s = maxside / max(w, h)
        im = im.resize((int(w * s), int(h * s)))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=85)
    return base64.b64encode(b.getvalue()).decode()


def ocr(raw):
    body = json.dumps({"model": MODEL, "prompt": PROMPT, "images": [prep_image(raw)],
                       "stream": False, "format": "json",
                       "options": {"temperature": 0, "num_ctx": 8192}}).encode()
    req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=300).read())["response"]
    data = json.loads(resp)
    rows = data.get("people", data) if isinstance(data, dict) else data
    out = []
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, str):
            r = {"name": r}
        name = (r.get("name") or "").strip()
        if len(name) < 3:
            continue
        ci = r.get("ci")
        ci = "".join(ch for ch in str(ci) if ch.isdigit()) if ci else None
        age = r.get("age") if isinstance(r.get("age"), int) else None
        out.append({"name": name, "age": age if (age and 0 < age < 120) else None,
                    "ci": ci or None, "origin": (r.get("origin") or None)})
    return out


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)

    print("Listando subcarpetas…", file=sys.stderr)
    folders = [it for it in list_drive_folder(FOLDER_ID) if it["type"] == "folder"]
    all_records, stats, n_new, n_cached = [], {}, 0, 0

    for f in folders:
        dest = dest_for(f["name"])
        if not dest:
            print(f"  ? subcarpeta sin destino: {f['name']}", file=sys.stderr)
            continue
        try:
            items = list_drive_folder(f["id"])
        except Exception as e:
            print(f"  ! {f['name']}: {e}", file=sys.stderr); continue
        imgs = [it for it in items if it["type"] == "image"]
        for it in imgs:
            try:
                raw = fetch(f"https://drive.google.com/uc?export=download&id={it['id']}", binary=True)
            except Exception as e:
                print(f"    ! descarga {it['name']}: {e}", file=sys.stderr); continue
            h = hashlib.sha1(raw).hexdigest()[:16]
            cf = CACHE / f"{h}.json"
            if cf.exists():
                recs = json.loads(cf.read_text(encoding="utf-8"))["records"]
                n_cached += 1
            else:
                try:
                    recs = ocr(raw)
                except Exception as e:
                    print(f"    ! OCR {it['name']}: {e}", file=sys.stderr); continue
                cf.write_text(json.dumps({"image": it["name"], "dest": dest, "records": recs},
                                         ensure_ascii=False), encoding="utf-8")
                n_new += 1
                print(f"    OCR {dest.split(':')[0][:22]:22} {it['name'][:34]:34} -> {len(recs)}", file=sys.stderr)
            for r in recs:
                all_records.append(dict(r, hospital=dest, source="Foto: " + it["name"],
                                        source_id=it["id"], source_kind="image"))
            stats[dest] = stats.get(dest, 0) + len(recs)

    # Guarda anti-encogimiento: si una corrida produce muchos menos registros que la
    # anterior (p.ej. listado de Drive falló transitoriamente), conserva la versión previa.
    if OUT.exists():
        try:
            old_n = len(json.loads(OUT.read_text(encoding="utf-8")).get("patients", []))
            if len(all_records) < old_n * 0.8:
                print(f"  ! OCR dio {len(all_records)} < {old_n} previos (posible fallo "
                      f"transitorio): conservo el anterior y no sobrescribo.", file=sys.stderr)
                return
        except Exception:
            pass

    OUT.write_text(json.dumps({"source": "OCR fotos (Qwen2.5-VL local)", "patients": all_records},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nOCR: {n_new} imágenes nuevas, {n_cached} desde caché. "
          f"{len(all_records)} registros -> {OUT}", file=sys.stderr)
    for d, c in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"   {c:4}  {d}", file=sys.stderr)


if __name__ == "__main__":
    main()
