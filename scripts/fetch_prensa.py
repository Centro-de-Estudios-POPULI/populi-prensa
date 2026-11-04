#!/usr/bin/env python3
"""
Scraper de menciones de prensa de POPULI vía Google News RSS.
Genera data/medios.json para la sección "En los Medios" del sitio.

Estrategia:
  - Consultas precisas (POPULI) = se confían tal cual.
  - Consultas por nombre/proyecto = se exige co-ocurrencia con "populi".
  - Curación opcional: config/destacados.json (manual, se ordena por fecha con el
    resto y gana sobre la copia de Google) y config/bloqueados.json (subcadenas a excluir).
  - ACUMULA: lo ya publicado en data/medios.json se conserva (Google News devuelve un
    subconjunto distinto cada día); las reglas y bloqueados.json se re-aplican a todo.
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

# (query, confiable).
#   confiable=True  → se queda todo lo que devuelve Google (la query ya restringe
#                     a POPULI por marca o por "Nombre + Populi", incluso si el
#                     titular no menciona "populi" porque la cita va en el cuerpo).
#   confiable=False → consulta amplia/temática: exige una SEÑAL POPULI en el titular.
QUERIES = [
    # Institucionales (marca inequívoca)
    ('"Centro de Estudios POPULI"', True),
    ('"Centro Populi"', True),
    ('"Fundación Populi"', True),
    # Voceros: «Nombre + Populi» o nombre completo ya restringen → se confía
    ('"Carlos Augusto Aranda Herrera"', True),
    ('"Carlos Aranda" Populi Bolivia', True),
    ('"Oscar Mario Tomianovic" Populi', True),
    ('"Oscar Tomianovic" Populi Bolivia', True),
    ('"Wilboor Brun" Populi', True),
    ('"Paola Condori Fernández" Populi', True),
    ('"Paola Condori" Populi Bolivia', True),
    # Voceros + temática económica: nombre + tema + país acotan lo suficiente
    # (homónimos del economista en Bolivia son raros; lo dudoso se purga vía bloqueados.json)
    ('"Carlos Aranda" economista Bolivia', True),
    ('"Carlos Aranda" análisis económico fiscal Bolivia', True),
    ('"Oscar Tomianovic" economía Bolivia', True),
    ('"Oscar Tomianovic" fiscal PIB inflación Bolivia', True),
    # Variantes que la prensa usa y la frase exacta no atrapa: «Oscar Mario / Óscar Mario /
    # Oscar M. Tomianovic» (firma de sus columnas) y «Carlos Augusto Aranda» sin el Herrera.
    # OJO: «Tomianovic» a secas trae al futbolista Mirko Tomianovic; «Carlos Aranda» a secas,
    # a un torero español y al cantante Juan Carlos Aranda.
    ('"Mario Tomianovic"', True),
    ('"Oscar M. Tomianovic"', True),
    ('"Carlos Augusto Aranda"', True),
    ('"economista Carlos Aranda"', True),
    # Restringidas por portal: Google News sí indexa estos sitios, así que captura
    # columnas firmadas y notas donde se cita a POPULI aunque el titular no diga "populi".
    ('"Carlos Aranda" site:eldeber.com.bo', True),
    ('"Oscar Tomianovic" site:eldeber.com.bo', True),
    ('"Mario Tomianovic" site:eldeber.com.bo', True),   # sus columnas: «Oscar Mario Tomianovic»
    ('"Oscar M. Tomianovic" site:eldeber.com.bo', True),   # …o «Oscar M. Tomianovic»
    ('"Centro de Estudios Populi" site:eldeber.com.bo', True),
    ('Populi economía site:eldeber.com.bo', False),
    # Nueva Presencia repite titulares rotativos en todas sus páginas: ver DOMINIOS_CON_SEÑAL.
    ('"Carlos Aranda" site:nuevapresencia.com', False),
    ('Populi site:nuevapresencia.com', False),
    # Plataformas/productos y temáticas (gated por señal en titular)
    ('"Atlas Fiscal Municipal" Bolivia', False),
    ('"Retrato Censal" Bolivia', False),
    ('"Observatorio de Finanzas Públicas" Bolivia', False),
    ('"Simulador de Coparticipación" Bolivia', False),
    ('Populi "libertad económica" Bolivia', False),
    ('Populi Bolivia análisis económico fiscal', False),
]

# Portales bolivianos adicionales (site:). Confiables: `site:` acota al portal y el
# término de marca/nombre evita homónimos; captura citas en el cuerpo, no solo el titular.
PORTALES = [
    'lostiempos.com', 'brujuladigital.net', 'opinion.com.bo',
    'noticiasfides.com', 'larazon.bo', 'correodelsur.com', 'eju.tv',   # la-razon.com ya no existe
    'unitel.bo', 'eldia.com.bo', 'elmundo.com.bo', 'erbol.com.bo', 'economy.com.bo',
    'urgente.bo', 'vision360.bo', 'elpotosi.net', 'publico.bo',
]
for _d in PORTALES:
    QUERIES.append((f'"Centro de Estudios Populi" site:{_d}', True))
    QUERIES.append((f'"Carlos Aranda" Populi site:{_d}', True))
    QUERIES.append((f'"Oscar Tomianovic" Populi site:{_d}', True))
    # la mayoría de las notas cita «el economista Carlos Aranda» u «Oscar Mario Tomianovic»
    # sin nombrar a POPULI: con «Populi» obligatorio se perdían (prueba del 03/11/2026:
    # +61 notas con cita verificada, con esta consulta y las variantes de nombre de arriba).
    QUERIES.append((f'"Carlos Aranda" economista site:{_d}', True))
    QUERIES.append((f'"Mario Tomianovic" site:{_d}', True))

# Excavar archivos viejos: consultas acotadas por año (Google News honra after:/before:).
# Términos inequívocos → confiables. Cubre años que el feed reciente no devuelve.
ARCHIVO_TERMINOS = [
    '"Centro de Estudios POPULI"',
    '"Fundación Populi"',
    '"Carlos Aranda" Populi',
    '"Oscar Tomianovic" Populi',
    '"Paola Condori" Populi',
    '"Carlos Aranda" site:eldeber.com.bo',
    '"Oscar Tomianovic" site:eldeber.com.bo',
]
ARCHIVO_ANIOS = list(range(2019, datetime.now().year + 1))   # incluye el año en curso
for _t in ARCHIVO_TERMINOS:
    for _y in ARCHIVO_ANIOS:
        QUERIES.append((f'{_t} after:{_y}-01-01 before:{_y + 1}-01-01', True))

# Señal de marca en el TITULAR (para queries temáticas gated). "populi" como PALABRA
# (con límites), lo que evita 'vozpopuli', 'populismo', 'popular', y descarta la
# locución latina 'vox populi'. El chequeo va sobre el titular SIN el nombre del medio.
POPULI_RE = re.compile(r'\bp[óo]puli\b', re.I)
# Locuciones que no son POPULI: vox/voz/box/vox-populi, «Salus Populi Romani» (abril-2025,
# entierro del papa Francisco), «laesae maiestatis populi Romani»…
LATIN_RE = re.compile(r'\b(?:vox|voz|box)[\s-]+p[óo]puli\b|\bsalus\s+p[óo]puli\b|\bp[óo]puli\s+romani\b', re.I)
# El nombre de un investigador en el titular también es señal (nunca se descarta una nota
# por llamarse «Carlos Aranda» u «Óscar Tomianovic»).
VOCEROS_RE = re.compile(r'\baranda\b|tomianovic|wilboor|paola condori', re.I)
OTRAS_SEÑALES = (
    'atlas fiscal', 'retrato censal',
    'simulador de coparticipación', 'observatorio de finanzas públicas',
)


def tiene_señal(headline):
    h = LATIN_RE.sub(' ', headline)
    return (bool(POPULI_RE.search(h)) or bool(VOCEROS_RE.search(h))
            or any(s in h.lower() for s in OTRAS_SEÑALES))


# Dominios cuyo nombre contiene "populi" (u homónimo) y NO son POPULI Bolivia.
DOMINIOS_BLOQUEADOS = {'vozpopuli.com'}
# Portales que repiten titulares rotativos («Últimas», «Más leídas») en TODAS sus páginas:
# cuando una nota con el nombre de un vocero pasa por ese bloque, Google indexa el portal
# entero con ese nombre (jun-nov 2026: 88 titulares de Nueva Presencia pasaron por el feed;
# 3 eran de POPULI, y los 3 lo dicen en el titular; las demás —Lula, el Evangelio, el clima—
# no lo nombran). Ahí se exige la señal en el TITULAR aunque la consulta sea confiable.
# Vale también para lo ya acumulado.
DOMINIOS_CON_SEÑAL = {'nuevapresencia.com'}


def estructural_ok(headline, dom, confiable=True):
    if dom in DOMINIOS_BLOQUEADOS:
        return False
    if (not confiable or dom in DOMINIOS_CON_SEÑAL) and not tiene_señal(headline):
        return False
    return True


# Nombre del medio por dominio: Google cambia el rótulo de la fuente («economy.com.bo»,
# «Economy», «Economy Bolivia») y el mismo medio salía con tres nombres.
NOMBRES_MEDIO = {
    'eldeber.com.bo': 'El Deber', 'correodelsur.com': 'Correo del Sur', 'eju.tv': 'eju.tv',
    'unitel.bo': 'Unitel', 'reduno.com.bo': 'Red Uno', 'atb.com.bo': 'ATB',
    'economy.com.bo': 'Economy', 'eldiario.net': 'El Diario', 'lapatria.bo': 'La Patria',
    'eldia.com.bo': 'El Día', 'larazon.bo': 'La Razón', 'vision360.bo': 'Visión 360',
    'brujuladigital.net': 'Brújula Digital', 'elmundo.com.bo': 'El Mundo',
    'asuntoscentrales.com': 'Asuntos Centrales', 'urgente.bo': 'Urgente.bo',
    'noticiasfides.com': 'Noticias Fides', 'elperiodico.com.bo': 'El Periódico',
    'elpotosi.net': 'El Potosí', 'lostiempos.com': 'Los Tiempos', 'opinion.com.bo': 'Opinión',
    'erbol.com.bo': 'Erbol', 'elpais.bo': 'El País Tarija', 'publico.bo': 'Público.bo',
    'americaeconomica.com': 'América Económica', 'connectas.org': 'Connectas',
    'boliviaverifica.bo': 'Bolivia Verifica', 'prensamercosur.org': 'Prensa Mercosur',
    'agrolatam.com': 'AgroLatam', 'nuevapresencia.com': 'Nueva Presencia', 'rfi.fr': 'RFI',
    'redpat.tv': 'Red PAT', 'bolpress.com': 'Bolpress', 'infobae.com': 'Infobae',
}


def nombre_medio(dom, crudo):
    partes = (dom or '').split('.')
    for i in range(len(partes) - 1):          # play.eldeber.com.bo -> eldeber.com.bo
        if '.'.join(partes[i:]) in NOMBRES_MEDIO:
            return NOMBRES_MEDIO['.'.join(partes[i:])]
    return limpiar_outlet(crudo)

MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
         'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']
STOP = {'el', 'la', 'los', 'las', 'de', 'del', 'y', 'digital', 'bolivia', 'com', 'bo'}
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
MAX_ITEMS = 1000        # el archivo acumula; con 150 lo nuevo (o la basura) expulsaba lo viejo
ANTIGUEDAD_DIAS = 3650  # hasta ~10 años atrás (para recuperar archivos antiguos)
BOT = timezone(timedelta(hours=-4))   # Bolivia (sin horario de verano)


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


def ts_de_fecha(s):
    """«6 de octubre, 2026» o «2026-10-06» -> timestamp (mediodía en Bolivia); 0 si no hay."""
    m = re.search(r'(\d{1,2}) de ([a-záéíóú]+),? (\d{4})', s or '', re.I)
    if m and m.group(2).lower() in MESES:
        f = (int(m.group(3)), MESES.index(m.group(2).lower()) + 1, int(m.group(1)))
    else:
        m = re.match(r'(\d{4})-(\d{2})-(\d{2})', s or '')
        if not m:
            return 0
        f = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return datetime(*f, 12, tzinfo=BOT).timestamp()


def clave(quote):
    return re.sub(r'\W+', '', quote.lower())[:60]


def normalizar(m, prio):
    """Entrada guardada (data/medios.json) o manual (destacados.json) -> forma interna.
    Lo manual puede traer solo href, quote, outlet y date (también ISO: 2026-10-23)."""
    g = re.search(r'domain=([^&]+)', m.get('favicon') or '')
    dom = g.group(1).lower() if g else dominio(m.get('href', ''))
    outlet = nombre_medio(dom, m.get('outlet', ''))
    ts = ts_de_fecha(m.get('date', ''))
    return {
        'outlet': outlet,
        'logo': m.get('logo') or iniciales(outlet),
        'favicon': m.get('favicon') or (f'https://www.google.com/s2/favicons?domain={dom}&sz=64' if dom else ''),
        'quote': (m.get('quote') or '').strip(),
        'date': fmt_fecha(datetime.fromtimestamp(ts, BOT)) if ts else m.get('date', ''),
        '_ts': ts, '_dom': dom, '_prio': prio,
        'href': m.get('href', ''),
    }


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

        # falsos positivos estructurales (vozpopuli.com; portales con barra rotativa) y
        # señal de marca sobre el titular (sin el nombre del medio) en consultas amplias
        if not estructural_ok(headline, dom, confiable):
            continue

        try:
            dt = parsedate_to_datetime(pub).astimezone(BOT)   # pubDate viene en GMT
        except Exception:
            dt = None
        if dt and dt < cutoff:
            continue

        outlet = nombre_medio(dom, outlet)
        out.append({
            'outlet': outlet,
            'logo': iniciales(outlet),
            'favicon': f'https://www.google.com/s2/favicons?domain={dom}&sz=64' if dom else '',
            'quote': headline,
            'date': fmt_fecha(dt) if dt else '',
            '_ts': dt.timestamp() if dt else 0,
            '_dom': dom, '_prio': 1,
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
    destacados = [normalizar(m, 0) for m in cargar_config('destacados.json')]  # manuales
    out_dir = ROOT / 'data'
    try:   # lo ya publicado: se conserva y vuelve a pasar por las reglas
        previos = json.loads((out_dir / 'medios.json').read_text(encoding='utf-8'))['medios']
    except Exception:
        previos = []
    previos = [m for m in (normalizar(x, 1) for x in previos) if estructural_ok(m['quote'], m['_dom'])]

    todos, errores = [], 0
    for query, confiable in QUERIES:
        try:
            res = parse_feed(fetch(query), confiable, cutoff)
            print(f'  [{len(res):2}] {query}', file=sys.stderr)
            todos.extend(res)
        except Exception as e:
            errores += 1
            print(f'  [ERR] {query} -> {e}', file=sys.stderr)

    # 01/11/2026 Google no respondió y se publicó un feed VACÍO: si falla la mitad de las
    # consultas, no se escribe nada y la Action termina en error (sin commit).
    if errores * 2 > len(QUERIES) or not todos:
        print(f'\n✗ {errores}/{len(QUERIES)} consultas fallaron: data/medios.json queda como estaba',
              file=sys.stderr)
        sys.exit(1)

    # bloqueo por subcadena (en titular, medio o url); lo manual no se bloquea
    def bloqueado(m):
        blob = f"{m['quote']} {m['outlet']} {m['href']}".lower()
        return m['_prio'] > 0 and any(b in blob for b in bloqueados)

    # dedupe por titular normalizado: gana lo manual y, si no, la publicación MÁS ANTIGUA
    # (el original frente a la copia en otro portal; la fecha real frente a la que Google
    # «actualiza» meses después). Se ordena por fecha desc al final.
    vistos, unicos = set(), []
    for m in sorted(destacados + todos + previos,
                    key=lambda x: (x['_prio'], x['_ts'] or float('inf'))):
        if bloqueado(m):
            continue
        key = clave(m['quote'])
        if key and key not in vistos:
            vistos.add(key)
            unicos.append(m)
    unicos.sort(key=lambda x: x['_ts'], reverse=True)

    for m in unicos:
        for k in ('_ts', '_dom', '_prio'):
            m.pop(k, None)

    medios = unicos[:MAX_ITEMS]

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
