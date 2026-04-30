# STOCKTAKE ONLINE

Mini app de Streamlit enfocada solo en el feature de Stocktake.
Esta carpeta es autocontenida y se puede mover de manera independiente.

## Deploy en Streamlit Community Cloud

- Repository: este mismo repo (o un repo nuevo solo con esta carpeta)
- Branch: la que quieras publicar
- Main file path: `STOCKTAKE_ONLINE/main_stocktake.py`
- Python version: `3.13`

## Secrets requeridos

Configuralos en `App settings -> Secrets`:

```toml
SUPABASE_URL = "https://..."
SUPABASE_SECRET_KEY = "..."

# Opcional: si no se define, login queda desactivado
APP_PASSWORD = "..."
```

## Dependencias

Usar `STOCKTAKE_ONLINE/requirements.txt` como archivo de dependencias.

## Run local

```bash
streamlit run STOCKTAKE_ONLINE/main_stocktake.py
```

La lógica de conteo y mapeo de variantes usa la misma DB actual mediante `scripts/stocktake.py` + Supabase.
# stocktake
