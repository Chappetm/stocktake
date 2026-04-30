import subprocess
import sys
import tempfile
from pathlib import Path

import streamlit as st


def _parse_kv_paths(stdout: str) -> dict[str, str]:
    """
    Parse stable key=value outputs printed by scripts, e.g.:
      STOCKTAKE_FINAL_COUNT_PATH=/abs/path/final_count.csv
      STOCKTAKE_UNMATCHED_PATH=/abs/path/unmatched_barcodes.xlsx
    """
    out: dict[str, str] = {}
    for line in (stdout or "").splitlines():
        line = line.strip()
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def render():
    st.header("🧮 Stocktake")
    st.caption(
        "Upload your scanner file(s) and the products file from Lightspeed. "
        "The system will generate final_count.csv and (if needed) unmatched_barcodes.xlsx."
    )

    script_stocktake = Path(__file__).resolve().parents[1] / "scripts" / "stocktake.py"

    # Persist results across reruns (download buttons should not clear the state)
    st.session_state.setdefault("stk_final_bytes", None)
    st.session_state.setdefault("stk_unmatched_bytes", None)
    st.session_state.setdefault("stk_logs", "")

    col1, col2 = st.columns(2)
    with col1:
        up_scanners = st.file_uploader(
            "📥 Upload scanner file(s) (xlsx/csv)",
            type=["xlsx", "xls", "csv"],
            accept_multiple_files=True,
            key="stk_scanners",
        )
    with col2:
        up_products = st.file_uploader(
            "📥 Upload file with all the products (csv/xlsx)",
            type=["xlsx", "xls", "csv"],
            key="stk_prd",
        )

    # Upload summary (user-friendly)
    scanner_names = [f.name for f in (up_scanners or [])]
    if scanner_names:
        st.success(f"✅ {len(scanner_names)} scanner file(s) selected")
    else:
        st.info("Upload one or more scanner files to continue.")

    if up_products is not None:
        st.success(f"✅ Products file selected: {up_products.name}")
    else:
        st.info("Upload the products file to continue.")

    # Clear stale outputs when the user has no inputs selected (e.g., returning to this page)
    if (not up_scanners) and (up_products is None):
        st.session_state["stk_final_bytes"] = None
        st.session_state["stk_unmatched_bytes"] = None
        st.session_state["stk_logs"] = ""

    can_run = bool(up_scanners) and (up_products is not None)
    run_btn = st.button("Run Stocktake", type="primary", disabled=not can_run)

    if run_btn:
        # Clear previous results
        st.session_state["stk_final_bytes"] = None
        st.session_state["stk_unmatched_bytes"] = None
        st.session_state["stk_logs"] = ""

        if not up_scanners or not up_products:
            st.error("❌ Please upload at least one scanner file and the products file.")
        elif not script_stocktake.exists():
            st.error(f"❌ Script not found: {script_stocktake}")
        else:
            with st.spinner("Processing stocktake..."):
                try:
                    with tempfile.TemporaryDirectory() as tmpdir:
                        tmpdir = Path(tmpdir).resolve()

                        prd_ext = up_products.name.split(".")[-1].lower()

                        p_prd = tmpdir / f"products.{prd_ext}"
                        outdir = tmpdir / "out"
                        outdir.mkdir(parents=True, exist_ok=True)

                        p_prd.write_bytes(up_products.getvalue())

                        cmd = [
                            sys.executable,
                            str(script_stocktake),
                        ]

                        for idx, up in enumerate(up_scanners, start=1):
                            ext = up.name.split(".")[-1].lower()
                            p_sc = tmpdir / f"scanner_{idx}.{ext}"
                            p_sc.write_bytes(up.getvalue())
                            cmd.extend(["--scanner", str(p_sc)])

                        cmd.extend(
                            [
                                "--products",
                                str(p_prd),
                                "--outdir",
                                str(outdir),
                            ]
                        )

                        result = subprocess.run(cmd, capture_output=True, text=True)

                        logs = ""
                        if result.stdout:
                            logs += result.stdout
                        if result.stderr:
                            logs += "\n" + result.stderr
                        st.session_state["stk_logs"] = logs.strip()

                        if result.returncode != 0:
                            st.error(f"❌ Stocktake failed (exit code {result.returncode}). See Log below.")
                        else:
                            paths = _parse_kv_paths(result.stdout or "")
                            final_path = Path(
                                paths.get("STOCKTAKE_FINAL_COUNT_PATH", outdir / "final_count.csv")
                            )
                            unmatched_path = Path(
                                paths.get("STOCKTAKE_UNMATCHED_PATH", outdir / "unmatched_barcodes.xlsx")
                            )

                            if final_path.exists():
                                st.session_state["stk_final_bytes"] = final_path.read_bytes()
                                st.success("✅ final_count.csv generated.")
                            else:
                                st.warning("⚠️ Script ran but final_count.csv was not found.")

                            if unmatched_path.exists():
                                st.session_state["stk_unmatched_bytes"] = unmatched_path.read_bytes()
                                st.info("📄 unmatched_barcodes.xlsx generated.")
                            else:
                                st.session_state["stk_unmatched_bytes"] = None
                                st.info("🎉 No unmatched barcodes.")
                except Exception as e:
                    st.error(f"❌ Error executing stocktake: {e}")

    with st.expander("Show technical details", expanded=False):
        st.text_area("Logs", st.session_state["stk_logs"] or "(No logs yet.)", height=220)

    if st.session_state["stk_final_bytes"] is not None:
        st.download_button(
            "⬇️ Download final_count.csv",
            data=st.session_state["stk_final_bytes"],
            file_name="final_count.csv",
            mime="text/csv",
            key="dl_final_stocktake",
        )

    if st.session_state["stk_unmatched_bytes"] is not None:
        st.download_button(
            "⬇️ Download unmatched_barcodes.xlsx",
            data=st.session_state["stk_unmatched_bytes"],
            file_name="unmatched_barcodes.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_unmatched_stocktake",
        )
