import hashlib
import io
import streamlit as st
import pandas as pd

MAX_MB = 50
_BYTES = MAX_MB * 1024 * 1024
MAX_EXCEL_ROWS = 500_000  # openpyxl is too slow above this; Excel hard limit is ~1M

st.set_page_config(page_title="Parquet Converter | Luminus", page_icon="📦", layout="wide")
st.title("Parquet → Excel / CSV Converter")


# ── helpers ───────────────────────────────────────────────────────────────────

def _to_excel(frames: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for sheet, df in frames.items():
            name = sheet[:31]
            df.to_excel(writer, index=False, sheet_name=name)
            writer.sheets[name].freeze_panes = "A2"
    return buf.getvalue()


def _to_csv(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def _stem(filename: str) -> str:
    return filename.rsplit(".", 1)[0]


def _size_label(n_bytes: int) -> str:
    return f"{n_bytes / 1024 / 1024:.1f} MB" if n_bytes >= 1024 * 1024 else f"{n_bytes / 1024:.0f} KB"


# ── upload ────────────────────────────────────────────────────────────────────

files = st.file_uploader(
    "Upload one or more .parquet files",
    type=["parquet"],
    accept_multiple_files=True,
)

if not files:
    st.info("Upload a .parquet file to get started.")
    st.stop()


# ── single-file mode ──────────────────────────────────────────────────────────

if len(files) == 1:
    f = files[0]
    stem = _stem(f.name)
    raw = f.getvalue()
    fhash = hashlib.md5(raw).hexdigest()[:8]
    size_mb = len(raw) / 1024 / 1024

    # Load data; for oversized files keep full frame for metrics, slice after slicer inputs
    try:
        if size_mb > MAX_MB:
            df_full = pd.read_parquet(io.BytesIO(raw))
            n_total = len(df_full)
            all_cols_full = df_full.columns.tolist()
        else:
            df = pd.read_parquet(io.BytesIO(raw))
            df_full = None
    except Exception as e:
        st.error(f"Could not read parquet file: {e}")
        st.stop()

    # File metrics
    disp_rows = n_total if df_full is not None else len(df)
    disp_cols = len(all_cols_full) if df_full is not None else len(df.columns)
    m1, m2, m3 = st.columns(3)
    m1.metric("Rows", f"{disp_rows:,}")
    m2.metric("Columns", str(disp_cols))
    m3.metric("File size", _size_label(len(raw)))
    if size_mb > MAX_MB:
        st.info(f"Large file — estimated memory usage: ~{size_mb * 4:.0f} MB (Streamlit Cloud limit ~1 GB)")

    st.divider()

    # Row slicer — only when file exceeds limit
    if df_full is not None:
        st.warning(f"File exceeds {MAX_MB} MB — select a row range to export a subset.")
        c1, c2 = st.columns(2)
        r0 = int(c1.number_input("Start row", 0, n_total - 1, 0, step=1_000))
        r1 = int(c2.number_input("End row", 1, n_total, min(50_000, n_total), step=1_000))
        if r0 >= r1:
            st.error("Start row must be less than end row.")
            st.stop()
        df = df_full.iloc[r0:r1].copy()
        st.caption(f"Slice: rows {r0:,} – {r1:,} ({r1 - r0:,} rows)")
        st.divider()

    # Column selector with Select all / Deselect all
    all_cols = df.columns.tolist()
    sel_key = f"sel_{fhash}"
    if sel_key not in st.session_state:
        st.session_state[sel_key] = all_cols

    lbl_col, b1, b2 = st.columns([4, 1, 1])
    lbl_col.caption(f"**Columns to export** — {len(st.session_state[sel_key])} of {len(all_cols)} selected")
    if b1.button("All", use_container_width=True, key=f"all_{fhash}"):
        st.session_state[sel_key] = all_cols
        st.rerun()
    if b2.button("None", use_container_width=True, key=f"none_{fhash}"):
        st.session_state[sel_key] = []
        st.rerun()

    selected = st.multiselect(
        "Columns",
        options=all_cols,
        key=sel_key,
        label_visibility="collapsed",
    )

    if not selected:
        st.warning("Select at least one column.")
        st.stop()

    st.divider()

    # Preview
    df_out = df[selected]
    st.dataframe(df_out.head(5), use_container_width=True)
    st.caption(f"Preview: first 5 of {len(df_out):,} rows × {len(selected)} columns")

    st.divider()

    too_large_for_excel = len(df_out) > MAX_EXCEL_ROWS
    if too_large_for_excel:
        st.warning(f"File has {len(df_out):,} rows — Excel skipped (limit ~500k rows). CSV only.")

    # Convert + downloads
    if st.button("Convert", type="primary", use_container_width=True):
        bar = st.progress(0, text="Writing CSV...")
        csv_bytes = _to_csv(df_out)
        st.session_state[f"_pq_{fhash}_csv"] = csv_bytes
        if not too_large_for_excel:
            bar.progress(20, text="Writing Excel...")
            excel_bytes = _to_excel({stem: df_out})
            st.session_state[f"_pq_{fhash}_excel"] = excel_bytes
        bar.progress(100, text="Ready.")

    if f"_pq_{fhash}_csv" in st.session_state:
        c1, c2 = st.columns(2)
        if not too_large_for_excel and f"_pq_{fhash}_excel" in st.session_state:
            c1.download_button(
                "⬇ Download Excel (.xlsx)",
                data=st.session_state[f"_pq_{fhash}_excel"],
                file_name=f"{stem}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
            c2.download_button(
                "⬇ Download CSV (.csv)",
                data=st.session_state[f"_pq_{fhash}_csv"],
                file_name=f"{stem}.csv",
                mime="text/csv",
                use_container_width=True,
            )
        else:
            st.download_button(
                "⬇ Download CSV (.csv)",
                data=st.session_state[f"_pq_{fhash}_csv"],
                file_name=f"{stem}.csv",
                mime="text/csv",
                use_container_width=True,
            )


# ── batch mode ────────────────────────────────────────────────────────────────

else:
    frames = {}

    # ── Main: per-file expanders ──────────────────────────────────────────────
    st.subheader(f"Batch mode — {len(files)} files")

    for idx, f in enumerate(files):
        stem = _stem(f.name)
        raw = f.getvalue()
        # idx prefix prevents DuplicateWidgetID when two files share the same stem
        slot = f"{idx}_{stem}"

        with st.expander(f"{f.name}  ({_size_label(len(raw))})"):
            if len(raw) > _BYTES:
                st.error(f"Exceeds {MAX_MB} MB limit. Upload this file alone to use the row slicer.")
                frames[slot] = None
                continue

            try:
                df = pd.read_parquet(io.BytesIO(raw))
            except Exception as e:
                st.error(f"Could not read file: {e}")
                frames[slot] = None
                continue
            cols = df.columns.tolist()

            m1, m2, m3 = st.columns(3)
            m1.metric("Rows", f"{len(df):,}")
            m2.metric("Cols", str(len(cols)))
            m3.metric("Size", _size_label(len(raw)))

            sel_key = f"b_{slot}"
            if sel_key not in st.session_state:
                st.session_state[sel_key] = cols

            st.caption(f"**Columns** ({len(st.session_state[sel_key])} of {len(cols)} selected)")
            b1, b2 = st.columns(2)
            if b1.button("Select all", use_container_width=True, key=f"all_b_{slot}"):
                st.session_state[sel_key] = cols
                st.rerun()
            if b2.button("Deselect all", use_container_width=True, key=f"none_b_{slot}"):
                st.session_state[sel_key] = []
                st.rerun()

            sel = st.multiselect("Columns", cols, key=sel_key, label_visibility="collapsed")
            frames[slot] = df[sel] if sel else None

    valid = {k: v for k, v in frames.items() if v is not None}

    if not valid:
        st.stop()

    excel_safe = {k: v for k, v in valid.items() if len(v) <= MAX_EXCEL_ROWS}
    csv_only_keys = [k for k in valid if k not in excel_safe]
    if csv_only_keys:
        st.warning(f"Excluded from Excel (>500k rows, CSV only): {', '.join(csv_only_keys)}")

    st.divider()
    if st.button("Convert all", type="primary", use_container_width=True):
        st.session_state.pop("_pq_batch_excel", None)
        bar = st.progress(0, text="Starting...")
        for i, name in enumerate(valid):
            bar.progress(int((i + 1) / len(valid) * 70), text=f"Preparing {name}...")
        bar.progress(75, text="Writing CSVs...")
        csv_map = {name: _to_csv(df) for name, df in valid.items()}
        st.session_state["_pq_batch_csvs"] = csv_map
        if excel_safe:
            bar.progress(85, text="Writing Excel...")
            st.session_state["_pq_batch_excel"] = _to_excel(excel_safe)
        bar.progress(100, text="Ready.")

    if "_pq_batch_excel" in st.session_state:
        st.download_button(
            "⬇ Download Excel (all sheets)",
            data=st.session_state["_pq_batch_excel"],
            file_name="parquet_export.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    if "_pq_batch_csvs" in st.session_state:
        st.caption("Individual CSVs:")
        dl_cols = st.columns(min(len(st.session_state["_pq_batch_csvs"]), 4))
        for i, (name, csv_bytes) in enumerate(st.session_state["_pq_batch_csvs"].items()):
            dl_cols[i % 4].download_button(
                f"⬇ {name}.csv",
                data=csv_bytes,
                file_name=f"{name}.csv",
                mime="text/csv",
                key=f"csv_dl_{name}",
                use_container_width=True,
            )
