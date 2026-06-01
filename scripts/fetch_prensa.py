#!/usr/bin/env python3
"""
Scraper de menciones de prensa de POPULI vía Google News RSS.
Genera data/medios.json para la sección "En los Medios" del sitio.

Estrategia:
  - Consultas precisas (POPULI) = se confían tal cual.
  - Consultas por nombre/proyecto = se exige co-ocurrencia con "populi".
  - Curación opcional: config/destacados.json (manual, se antepone) y
    config/bloqueados.json (lista de subcadenas a excluir).
"""
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (query, confiable). confiable=True → se queda todo; False → exige 'populi'.
QUERIES = [
    ('"Centro de Estudios POPULI"', True),
    ('"Centro Populi"', True),
    ('"Fundación Populi"', True),
    ('"Carlos Aranda" Populi Bolivia', False),
    ('"Oscar Tomianovic" Populi', False),
    ('"Wilboor Brun" Populi', False),
    ('"Atlas Fiscal Municipal" Bolivia', False),
    ('"Retrato Censal" Bolivia Populi', False),
    ('Populi "libertad económica" Bolivia', False),
]

MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
         'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']
STOP = {'el', 'la', 'los', 'las', 'de', 'del', 'y', 'digital', 'bolivia', 'com', 'bo'}
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
MAX_ITEMS = 12
ANTIGUEDAD_DIAS = 900  # ignora menciones más viejas que ~2.5 años


def fetch(query):
    q = urllib.parse.quote(query)
    url = f'https://news.google.com/rss/search?q={q}&hl=es-419&gl=BO&ceid=BO:es'
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=25) as r:  # urllib sigue el 302
        return r.read()


def limpiar_outlet(name):
    name = re.sub(r'\s*-\s*Bolivia\s*$', '', name, flags=re.I)
    name = re.sub(r'\s+Digital\s*$', '', name, flags=re.I)
    return name.strip() or 'Medio'


def iniciales(outlet):
    palabras = [w for w in re.findall(r'[A-Za-zÁÉÍÓÚÑáéíóúñ0-9]+', outlet)
                if w.lower() not in STOP]
    if not palabras:
        palabras = outlet.split()
    return (''.join(w[0] for w in palabras[:3]).upper() or outlet[:2].upper())


def dominio(src_url):
    try:
        host = urllib.parse.urlparse(src_url).netloc.lower()
        return host[4:] if host.startswith('www.') else host
    except Exception:
        return ''


def fmt_fecha(dt):
    return f'{dt.day} de {MESES[dt.month - 1]}, {dt.year}'


def parse_feed(xml_bytes, confiable, cutoff):
    root = ET.fromstring(xml_bytes)
    out = []
    for item in root.iter('item'):
        title = (item.findtext('title') or '').strip()
        link = (item.findtext('link') or '').strip()
        pub = item.findtext('pubDate')
        src_el = item.find('source')
        outlet = (src_el.text or '').strip() if src_el is not None else ''
        dom = dominio(src_el.get('url')) if src_el is not None else ''

        headline = title
        if outlet and headline.endswith(f' - {outlet}'):
            headline = headline[: -(len(outlet) + 3)].strip()
        elif ' - ' in headline:
            headline, outlet2 = headline.rsplit(' - ', 1)
            outlet = outlet or outlet2.strip()

        if not confiable and 'populi' not in title.lower():
            continue

        try:
            dt = parsedate_to_datetime(pub)
        except Exception:
            dt = None
        if dt and dt < cutoff:
            continue

        outlet = limpiar_outlet(outlet)
        out.append({
            'outlet': outlet,
            'logo': iniciales(outlet),
            'favicon': f'https://www.google.com/s2/favicons?domain={dom}&sz=64' if dom else '',
            'quote': headline,
            'date': fmt_fecha(dt) if dt else '',
            '_ts': dt.timestamp() if dt else 0,
            'href': link,
        })
    return out


def cargar_config(nombre):
    f = ROOT / 'config' / nombre
    if f.exists():
        try:
            return json.loads(f.read_text(encoding='utf-8'))
        except Exception:
            pass
    return []


def main():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=ANTIGUEDAD_DIAS)
    bloqueados = [b.lower() for b in cargar_config('bloqueados.json')]
    destacados = cargar_config('destacados.json')  # entradas manuales

    todos = []
    for query, confiable in QUERIES:
        try:
            res = parse_feed(fetch(query), confiable, cutoff)
            print(f'  [{len(res):2}] {query}', file=sys.stderr)
            todos.extend(res)
        except Exception as e:
            print(f'  [ERR] {query} -> {e}', file=sys.stderr)

    # bloqueo por subcadena (en titular, medio o url)
    def bloqueado(m):
        blob = f"{m['quote']} {m['outlet']} {m['href']}".lower()
        return any(b in blob for b in bloqueados)

    # dedupe por titular normalizado, ordenado por fecha desc
    vistos, unicos = set(), []
    for m in sorted(todos, key=lambda x: x['_ts'], reverse=True):
        if bloqueado(m):
            continue
        key = re.sub(r'\W+', '', m['quote'].lower())[:60]
        if key and key not in vistos:
            vistos.add(key)
            unicos.append(m)

    for m in unicos:
        m.pop('_ts', None)

    medios = (destacados + unicos)[:MAX_ITEMS]

    out_dir = ROOT / 'data'
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        'actualizado': now.isoformat(),
        'total': len(medios),
        'medios': medios,
    }
    (out_dir / 'medios.json').write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n✓ {len(medios)} menciones → data/medios.json', file=sys.stderr)


if __name__ == '__main__':
    main()
