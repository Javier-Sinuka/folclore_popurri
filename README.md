# Repertorio Folclórico Argentino

Colección personal de letras de folklore argentino, organizada como un documento [Quarkdown](https://github.com/iamgio/quarkdown) (`repertorio_folclore.qd`).

El repertorio incluye zambas, chacareras, vidalas y otras piezas del cancionero popular argentino — desde clásicos como *Alfonsina y el Mar* y *El Arriero* hasta temas menos difundidos del noroeste argentino.

## Página web

El documento se compila automáticamente a PDF con cada push a `main` y se publica en GitHub Pages:

🎵 **[Ver Repertorio Folclórico](https://javier-sinuka.github.io/folclore_popurri/)**

## Contenido del repositorio

| Archivo | Descripción |
|---|---|
| `repertorio_folclore.qd` | Documento principal con las letras, escrito en Quarkdown |
| `fetch_lyrics_ovh.py` | Script Python para completar letras faltantes usando la API de [lyrics.ovh](https://lyrics.ovh) |

## Completar letras faltantes

Las canciones sin letra tienen el marcador `_Espacio para la letra._`. Para intentar completarlas automáticamente desde la playlist de Spotify:

```bash
# Vista previa sin modificar el archivo
python3 fetch_lyrics_ovh.py --dry-run

# Aplicar los cambios
python3 fetch_lyrics_ovh.py --write
```
