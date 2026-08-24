# Parquet Converter

Convert `.parquet` files to Excel or CSV — no Python required to use.

## What it does

- **Single file:** column selector, row slicer for oversized files (>50 MB), Excel + CSV download
- **Batch mode:** upload multiple files → one Excel with one sheet per file
- Frozen header row in all Excel outputs

## Run

```bash
cd "00. Parquet Converter"
python -m streamlit run app.py
```

Then share the browser URL with colleagues.

## First-time setup

```bash
python -m pip install -r requirements.txt
```
