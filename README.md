# populi-prensa

Monitor automático de menciones de prensa del **Centro de Estudios POPULI**.
Consulta Google News y genera `data/medios.json`, que alimenta la sección
**"En los Medios"** del sitio populi.org.bo.

## Cómo funciona

1. `scripts/fetch_prensa.py` consulta Google News RSS para varios términos
   (institución, voceros, proyectos).
2. Filtra por relevancia (co-ocurrencia con "Populi" en consultas amplias),
   deduplica, limpia el nombre del medio y añade su favicon.
3. **Acumula**: suma lo nuevo a lo ya publicado en `data/medios.json` (Google
   News devuelve un subconjunto distinto cada día) y re-aplica las reglas y
   `bloqueados.json` a todo. Si falla más de la mitad de las consultas (Google
   responde 503), no escribe nada y la Action termina en error.
4. La **GitHub Action** (`.github/workflows/update_prensa.yml`) lo ejecuta a
   diario y commitea los cambios.

Solo usa la **librería estándar de Python** (sin dependencias).

## Curación (opcional)

- `config/destacados.json` — menciones manuales (TV/radio/prensa que no indexa
  Google News). Basta `href`, `quote`, `outlet` y `date` (`"2026-10-23"` o
  `"23 de octubre, 2026"`); el logo y el favicon salen del dominio. Se ordenan
  por fecha con el resto y ganan sobre la copia de Google del mismo titular.
- `config/bloqueados.json` — lista de subcadenas; cualquier mención cuyo
  titular o medio las contenga se excluye (la URL guardada es la de Google
  News, así que bloquear por la URL del medio no funciona).

## Uso local

```bash
python scripts/fetch_prensa.py
```

## Consumo desde el sitio

El frontend Astro lee este JSON en build-time desde:

```
https://raw.githubusercontent.com/Centro-de-Estudios-POPULI/populi-prensa/main/data/medios.json
```

con una copia local como respaldo (degradación elegante si la fuente falla).
