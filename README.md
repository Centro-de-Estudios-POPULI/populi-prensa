# populi-prensa

Monitor automático de menciones de prensa del **Centro de Estudios POPULI**.
Consulta Google News y genera `data/medios.json`, que alimenta la sección
**"En los Medios"** del sitio populi.org.bo.

## Cómo funciona

1. `scripts/fetch_prensa.py` consulta Google News RSS para varios términos
   (institución, voceros, proyectos).
2. Filtra por relevancia (co-ocurrencia con "Populi" en consultas amplias),
   deduplica, limpia el nombre del medio y añade su favicon.
3. Escribe `data/medios.json` (las 12 menciones más recientes).
4. La **GitHub Action** (`.github/workflows/update_prensa.yml`) lo ejecuta a
   diario y commitea los cambios.

Solo usa la **librería estándar de Python** (sin dependencias).

## Curación (opcional)

- `config/destacados.json` — lista de menciones manuales que se anteponen
  (útil para TV/radio/prensa impresa que no indexa Google News).
- `config/bloqueados.json` — lista de subcadenas; cualquier mención cuyo
  titular, medio o URL las contenga se excluye.

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
