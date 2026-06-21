# SOLO INVERSIONES

Web que sigue las empresas cotizadas que se mencionan en el grupo de Telegram.
Se actualiza **sola cada día**: un bot lee los mensajes nuevos, una IA extrae las
empresas y la web se vuelve a publicar automáticamente.

```
mensajes en Telegram  →  GitHub Actions (diario)  →  data/companies.json  →  web en Netlify
```

## Qué hay aquí

| Archivo | Para qué |
|---|---|
| `index.html` | La web (lee `data/companies.json` y los precios en vivo). |
| `data/companies.json` | La lista de empresas (la actualiza el motor). |
| `engine/update.py` | El motor: lee Telegram + extrae empresas con IA. |
| `.github/workflows/daily.yml` | Lo ejecuta cada día a las 06:00 UTC. |
| `state.json` | Recuerda por qué mensaje va, para no repetir. |

---

## Puesta en marcha (una sola vez)

### 1. Sube esta carpeta a un repositorio de GitHub
- Crea una cuenta en https://github.com si no tienes.
- Crea un repositorio nuevo (p.ej. `solo-inversiones`), **privado o público**.
- Sube todos estos archivos (botón "Add file → Upload files", arrastra la carpeta).

### 2. Añade las claves como *Secrets*
En el repo: **Settings → Secrets and variables → Actions → New repository secret**.
Crea estos tres:

| Nombre | Valor |
|---|---|
| `TELEGRAM_TOKEN` | el token de tu bot de @BotFather |
| `OPENAI_API_KEY` | tu key de https://platform.openai.com (API keys) |
| `FINNHUB_KEY` | tu key de https://finnhub.io (para validar tickers) |

> Nunca escribas estas claves dentro de los archivos: van solo en Secrets.

### 3. Publica la web en Netlify
- Entra en https://app.netlify.com → **Add new site → Import an existing project**.
- Conecta tu GitHub y elige el repo `solo-inversiones`.
- Build command: *(vacío)* · Publish directory: `.`
- Deploy. Te da un enlace `https://....netlify.app` → ese es el que compartes.

Cada vez que el motor actualice la lista, Netlify vuelve a publicar solo.

### 4. Pruébalo sin esperar a mañana
- En el repo: pestaña **Actions → "Actualizar empresas (diario)" → Run workflow**.
- Mira el registro: dirá cuántos mensajes leyó y qué empresas añadió.

---

## Notas
- El bot solo lee mensajes **desde que entró** al grupo (sin histórico). El histórico
  inicial ya viene cargado en `data/companies.json`.
- Telegram guarda los mensajes para el bot ~24 h, por eso el motor corre **a diario**.
- Coste: GitHub Actions y Netlify son gratis para este uso; la IA son céntimos al mes.
- Para cambiar la hora, edita el `cron` en `.github/workflows/daily.yml`.
