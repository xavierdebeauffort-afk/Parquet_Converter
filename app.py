import io
import streamlit as st
import pandas as pd

MAX_MB = 50
_BYTES = MAX_MB * 1024 * 1024

st.set_page_config(page_title="Parquet Converter", page_icon="📦", layout="wide")
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


# ── upload ────────────────────────────────────────────────────────────────────

files = st.file_uploader(
    "Upload one or more .parquet files",
    type=["parquet"],
    accept_multiple_files=True,
)

if not files:
    st.info("Upload one or more .parquet files to get started.")
    st.stop()


# ── single-file mode ──────────────────────────────────────────────────────────

if len(files) == 1:
    f = files[0]
    stem = _stem(f.name)
    raw = f.getvalue()

    if len(raw) > _BYTES:
        st.warning(
            f"**{f.name}** is {len(raw) / 1024 / 1024:.1f} MB — over the {MAX_MB} MB limit. "
            "Select a row range below to export a subset."
        )
        df_full = pd.read_parquet(io.BytesIO(raw))
        n = len(df_full)
        st.caption(f"Full file: {n:,} rows × {len(df_full.columns)} columns")

        c1, c2 = st.columns(2)
        r0 = int(c1.number_input("Start row", min_value=0, max_value=n - 1, value=0, step=1_000))
        r1 = int(c2.number_input("End row", min_value=1, max_value=n, value=min(50_000, n), step=1_000))

        if r0 >= r1:
            st.error("Start row must be less than end row.")
            st.stop()

        df = df_full.iloc[r0:r1].copy()
        st.caption(f"Slice: rows {r0:,} – {r1:,}  ({r1 - r0:,} rows)")
    else:
        df = pd.read_parquet(io.BytesIO(raw))

    all_cols = df.columns.tolist()
    selected = st.multiselect("Columns to export", options=all_cols, default=all_cols)

    if not selected:
        st.warning("Select at least one column.")
        st.stop()

    df = df[selected]
    st.dataframe(df.head(500), use_container_width=True)
    st.caption(f"Preview: first 500 of {len(df):,} rows × {len(selected)} columns")

    if st.button("Convert", type="primary"):
        bar = st.progress(0, text="Writing Excel...")
        excel_bytes = _to_excel({stem: df})
        bar.progress(80, text="Writing CSV...")
        csv_bytes = _to_csv(df)
        bar.progress(100, text="Ready.")
        st.session_state["_pq_excel"] = excel_bytes
        st.session_state["_pq_csv"] = csv_bytes
        st.session_state["_pq_stem"] = stem

    # download buttons persist via session state until a different file is loaded
    if st.session_state.get("_pq_stem") == stem:
        c1, c2 = st.columns(2)
        c1.download_button(
            "⬇ Download Excel (.xlsx)",
            data=st.session_state["_pq_excel"],
            file_name=f"{stem}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        c2.download_button(
            "⬇ Download CSV (.csv)",
            data=st.session_state["_pq_csv"],
            file_name=f"{stem}.csv",
            mime="text/csv",
        )


# ── batch mode ────────────────────────────────────────────────────────────────

else:
    st.subheader(f"Batch mode — {len(files)} files")
    frames = {}

    for f in files:
        stem = _stem(f.name)
        raw = f.getvalue()

        with st.expander(f"{f.name}  ({len(raw) / 1024 / 1024:.1f} MB)"):
            if len(raw) > _BYTES:
                st.error(
                    f"Exceeds {MAX_MB} MB limit. Upload this file alone to use the row slicer."
                )
                frames[stem] = None
                continue

            df = pd.read_parquet(io.BytesIO(raw))
            cols = df.columns.tolist()
            sel = st.multiselect("Columns to include", cols, default=cols, key=f"b_{stem}")
            frames[stem] = df[sel] if sel else None
            st.caption(f"{len(df):,} rows × {len(sel)} columns selected")

    valid = {k: v for k, v in frames.items() if v is not None}

    if not valid:
        st.stop()

    if st.button("Convert all to Excel", type="primary"):
        bar = st.progress(0, text="Starting...")
        for i, name in enumerate(valid):
            bar.progress(int((i + 1) / len(valid) * 90), text=f"Preparing {name}...")
        bar.progress(90, text="Writing Excel...")
        excel_bytes = _to_excel(valid)
        bar.progress(100, text="Ready.")
        st.session_state["_pq_batch_excel"] = excel_bytes

    if "_pq_batch_excel" in st.session_state:
        st.download_button(
            "⬇ Download Excel (all sheets)",
            data=st.session_state["_pq_batch_excel"],
            file_name="parquet_export.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
