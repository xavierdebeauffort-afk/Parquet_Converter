import hashlib
import io
import streamlit as st
import pandas as pd
import pyarrow.parquet as pq

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


@st.cache_data(show_spinner="Reading file metadata...")
def _read_metadata(raw: bytes) -> tuple:
    # reads schema + row count only — zero column data loaded into memory
    pf = pq.ParquetFile(io.BytesIO(raw))
    schema = pf.schema_arrow
    return pf.metadata.num_rows, schema.names, [str(t) for t in schema.types]


@st.cache_data(show_spinner="Loading selected columns...")
def _read_columns(raw: bytes, columns: tuple) -> pd.DataFrame:
    # columns is a tuple so @st.cache_data can hash it
    return pq.read_table(io.BytesIO(raw), columns=list(columns)).to_pandas()


@st.cache_data(show_spinner=False)
def _read_preview(raw: bytes, columns: tuple) -> pd.DataFrame:
    # reads first row group only — much cheaper than loading the full file
    pf = pq.ParquetFile(io.BytesIO(raw))
    return pf.read_row_group(0, columns=list(columns)).to_pandas().head(10)


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

    # Read schema + row count only — no column data loaded yet
    try:
        n_total, all_cols, _ = _read_metadata(raw)
    except Exception as e:
        st.error(f"Could not read parquet file: {e}")
        st.stop()

    m1, m2, m3 = st.columns(3)
    m1.metric("Rows", f"{n_total:,}")
    m2.metric("Columns", str(len(all_cols)))
    m3.metric("File size", _size_label(len(raw)))
    if size_mb > MAX_MB:
        st.info(f"Large file — estimated full load: ~{size_mb * 4:.0f} MB. Select only the columns you need below.")

    st.divider()

    # Row slicer for large files
    if size_mb > MAX_MB:
        st.warning(f"File exceeds {MAX_MB} MB — select a row range to limit memory usage.")
        c1, c2 = st.columns(2)
        r0 = int(c1.number_input("Start row", 0, n_total - 1, 0, step=1_000))
        r1 = int(c2.number_input("End row", 1, n_total, min(50_000, n_total), step=1_000))
        if r0 >= r1:
            st.error("Start row must be less than end row.")
            st.stop()
        st.caption(f"Slice: rows {r0:,} – {r1:,} ({r1 - r0:,} rows)")
        st.divider()
    else:
        r0, r1 = 0, n_total

    # Column selector — no data loaded until Convert is clicked
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

    # Estimated memory after column selection
    sel_frac = len(selected) / len(all_cols)
    row_frac = (r1 - r0) / n_total if n_total > 0 else 1.0
    est_mb = size_mb * 4 * sel_frac * row_frac
    if est_mb > 400:
        st.warning(f"Estimated memory for this selection: ~{est_mb:.0f} MB — may be slow on Streamlit Cloud free tier.")

    st.divider()

    # Preview — reads first row group of selected columns only
    try:
        preview_df = _read_preview(raw, tuple(selected))
        st.dataframe(preview_df, use_container_width=True)
        st.caption(f"Preview: first 10 of {r1 - r0:,} rows × {len(selected)} columns")
    except Exception:
        st.caption("Preview unavailable for this file.")

    st.divider()

    n_out = r1 - r0
    too_large_for_excel = n_out > MAX_EXCEL_ROWS
    if too_large_for_excel:
        st.warning(f"Row range has {n_out:,} rows — Excel skipped (limit ~500k rows). CSV only.")

    # Data is only loaded here, not before
    if st.button("Convert", type="primary", use_container_width=True):
        try:
            bar = st.progress(0, text="Loading selected columns...")
            df_out = _read_columns(raw, tuple(selected))
            if r0 > 0 or r1 < n_total:
                df_out = df_out.iloc[r0:r1].reset_index(drop=True)
            bar.progress(50, text="Writing CSV...")
            st.session_state[f"_pq_{fhash}_csv"] = _to_csv(df_out)
            if not too_large_for_excel:
                bar.progress(70, text="Writing Excel...")
                st.session_state[f"_pq_{fhash}_excel"] = _to_excel({stem: df_out})
            bar.progress(100, text="Ready.")
        except Exception as e:
            st.error(f"Conversion failed: {e}")

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
    # file_selections: slot → (raw_bytes, selected_cols_tuple, n_rows)
    # DataFrames are NOT loaded during the expander loop — only at Convert time
    file_selections = {}

    st.subheader(f"Batch mode — {len(files)} files")

    for idx, f in enumerate(files):
        stem = _stem(f.name)
        raw = f.getvalue()
        # idx prefix prevents DuplicateWidgetID when two files share the same stem
        slot = f"{idx}_{stem}"

        with st.expander(f"{f.name}  ({_size_label(len(raw))})"):
            if len(raw) > _BYTES:
                st.error(f"Exceeds {MAX_MB} MB limit. Upload this file alone to use the row slicer.")
                continue

            try:
                n_rows, col_names, _ = _read_metadata(raw)
            except Exception as e:
                st.error(f"Could not read file: {e}")
                continue

            m1, m2, m3 = st.columns(3)
            m1.metric("Rows", f"{n_rows:,}")
            m2.metric("Cols", str(len(col_names)))
            m3.metric("Size", _size_label(len(raw)))

            sel_key = f"b_{slot}"
            if sel_key not in st.session_state:
                st.session_state[sel_key] = col_names

            st.caption(f"**Columns** ({len(st.session_state[sel_key])} of {len(col_names)} selected)")
            b1, b2 = st.columns(2)
            if b1.button("Select all", use_container_width=True, key=f"all_b_{slot}"):
                st.session_state[sel_key] = col_names
                st.rerun()
            if b2.button("Deselect all", use_container_width=True, key=f"none_b_{slot}"):
                st.session_state[sel_key] = []
                st.rerun()

            sel = st.multiselect("Columns", col_names, key=sel_key, label_visibility="collapsed")
            if sel:
                file_selections[slot] = (raw, tuple(sel), n_rows)

    if not file_selections:
        st.stop()

    excel_safe_keys = {k for k, v in file_selections.items() if v[2] <= MAX_EXCEL_ROWS}
    csv_only_keys = [k for k in file_selections if k not in excel_safe_keys]
    if csv_only_keys:
        st.warning(f"Excluded from Excel (>500k rows, CSV only): {', '.join(csv_only_keys)}")

    st.divider()
    if st.button("Convert all", type="primary", use_container_width=True):
        st.session_state.pop("_pq_batch_excel", None)
        try:
            bar = st.progress(0, text="Starting...")
            frames_excel = {}
            csv_map = {}
            total = len(file_selections)
            for i, (slot, (raw, sel_cols, _)) in enumerate(file_selections.items()):
                bar.progress(int((i + 1) / total * 70), text=f"Loading {slot}...")
                df = _read_columns(raw, sel_cols)
                csv_map[slot] = _to_csv(df)
                if slot in excel_safe_keys:
                    frames_excel[slot] = df
            bar.progress(80, text="Writing CSVs...")
            st.session_state["_pq_batch_csvs"] = csv_map
            if frames_excel:
                bar.progress(88, text="Writing Excel...")
                st.session_state["_pq_batch_excel"] = _to_excel(frames_excel)
            bar.progress(100, text="Ready.")
        except Exception as e:
            st.error(f"Conversion failed: {e}")

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
