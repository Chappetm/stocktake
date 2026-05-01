from __future__ import annotations

import argparse
import logging
import math
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# Column heuristics
BARCODE_CANDS = ["barcode", "bar code", "code", "ean", "upc", "codigo", "código"]
COUNT_CANDS = ["count", "qty", "quantity", "cantidad", "scans", "total units", "total unit", "units"]
PRODUCT_NAME_CANDS = ["product name", "product", "name", "nombre", "description"]
NOTES_CANDS = ["notes", "note", "comments", "comment", "notas"]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=True, description="Simple stocktake (scanner files + products list).")

    # Preferred: repeatable --scanner
    p.add_argument(
        "--scanner",
        action="append",
        dest="scanners",
        default=[],
        help="Path to a scanner file (xlsx/csv). Provide this flag multiple times to merge many files.",
    )

    # Legacy (kept for backward compatibility)
    p.add_argument("--scanner1", required=False, default="", help="Legacy: Path to scanner1 file (xlsx/csv)")
    p.add_argument("--scanner2", required=False, default="", help="Legacy: Path to scanner2 file (xlsx/csv) (optional)")

    p.add_argument("--products", required=True, help="Path to products file (csv/xlsx)")
    p.add_argument("--outdir", required=True, help="Output directory")

    args = p.parse_args()

    # If the new flag was not used, fall back to legacy flags
    if not args.scanners:
        legacy = []
        if str(args.scanner1).strip():
            legacy.append(args.scanner1)
        if str(args.scanner2).strip():
            legacy.append(args.scanner2)
        args.scanners = legacy

    # Clean empties
    args.scanners = [s for s in (args.scanners or []) if str(s).strip()]

    if not args.scanners:
        p.error(
            "At least one scanner file is required. Use --scanner <path> (repeatable) or legacy --scanner1/--scanner2."
        )

    return args


def _norm(s) -> str:
    if s is None:
        return ""
    return str(s).strip().lower().replace("\n", " ").replace("\r", " ")


def _find_col(df: pd.DataFrame, cands: list[str]) -> str | None:
    norm2real = {_norm(c): c for c in df.columns}
    # Exact match
    for c in cands:
        if _norm(c) in norm2real:
            return norm2real[_norm(c)]
    # Contains match
    for real in df.columns:
        n = _norm(real)
        if any(_norm(c) in n for c in cands):
            return real
    return None


def _clean_barcode(x) -> str:
    """Normalize input into a digits-only barcode string (handles scientific notation and '.0' suffix)."""
    if x is None:
        return ""
    if isinstance(x, float) and math.isnan(x):
        return ""

    s = str(x).strip()
    if s == "":
        return ""

    s = s.replace(",", "")

    try:
        if "e" in s.lower():
            d = Decimal(s)
            s = str(d.quantize(Decimal(1)))
    except InvalidOperation:
        pass

    if s.endswith(".0"):
        s = s[:-2]

    s = "".join(ch for ch in s if ch.isdigit())
    return s


def _read_table(path, header=0):
    """
    Read csv/xlsx into a DataFrame.

    Note:
    - pandas.read_excel() does NOT accept header="infer".
      It only accepts int | list[int] | None.
    - pandas.read_csv() accepts "infer", but using 0 is equivalent for our case.
    """
    suffix = str(path).lower()

    # Normalize header for Excel compatibility
    excel_header = 0 if header == "infer" else header

    if suffix.endswith((".xlsx", ".xls")):
        return pd.read_excel(path, header=excel_header, dtype=str)
    if suffix.endswith(".csv"):
        return pd.read_csv(path, header=header, dtype=str)

    raise ValueError(f"Unsupported file type: {path}")


def _load_scanner(path: Path) -> pd.DataFrame:
    # 1) Initial read with default header detection
    df = _read_table(path, header="infer")
    df.columns = [str(c).strip() if c is not None else "" for c in df.columns]

    if df.empty:
        return pd.DataFrame(columns=["barcode", "count", "scanner_name", "scanner_notes"])

    bcol = _find_col(df, BARCODE_CANDS)
    ccol = _find_col(df, COUNT_CANDS) if bcol else None

    # New scanner template support:
    # - Prefer "Total units" if present
    # - If formula results are missing/zero, compute totals from pack-size columns (e.g. 30/24/16/6/4/1)
    if bcol and not ccol:
        total_units_col = _find_col(df, ["total units", "total unit", "total_units"])

        pack_cols = [c for c in df.columns if _norm(c).isdigit()]
        computed_col = None
        if pack_cols:
            total = 0
            for c in pack_cols:
                size = int(_norm(c))
                total += pd.to_numeric(df[c], errors="coerce").fillna(0) * size
            df["_computed_total_units"] = total
            computed_col = "_computed_total_units"

        if total_units_col:
            tu = pd.to_numeric(df[total_units_col], errors="coerce").fillna(0)
            if tu.sum() > 0:
                ccol = total_units_col
            elif computed_col and pd.to_numeric(df[computed_col], errors="coerce").fillna(0).sum() > 0:
                logger.warning("'Total units' appears empty/zero; using computed totals from pack-size columns.")
                ccol = computed_col
            else:
                ccol = total_units_col
        elif computed_col:
            ccol = computed_col

    # 2) Fallback: if barcode column is not detected, assume there is no header row.
    if not bcol:
        df = _read_table(path, header=None)

        if df.shape[1] >= 2:
            df = df.iloc[:, :2].copy()
            df.columns = ["barcode", "count"]
        else:
            df.columns = ["barcode"]

        df["barcode"] = df["barcode"].map(_clean_barcode)
        df = df[df["barcode"] != ""]

        if "count" in df.columns:
            df["count"] = pd.to_numeric(df["count"], errors="coerce").fillna(0).round(0).astype(int)
            df = df[df["count"] > 0]
            agg = df.groupby("barcode", as_index=False)["count"].sum()

        else:
            df["_u"] = 1
            agg = df.groupby("barcode", as_index=False)["_u"].sum().rename(columns={"_u": "count"})

        agg["scanner_name"] = None
        agg["scanner_notes"] = None

        return agg

    # Detect optional text columns (product name and notes from scanner)
    pncol = _find_col(df, PRODUCT_NAME_CANDS)
    notecol = _find_col(df, NOTES_CANDS)

    # 3) Normal path (barcode column detected)
    df[bcol] = df[bcol].map(_clean_barcode)
    df = df[df[bcol] != ""]

    if ccol:
        df[ccol] = pd.to_numeric(df[ccol], errors="coerce").fillna(0).round(0).astype(int)
        df = df[df[ccol] > 0]
        agg = df.groupby(bcol, as_index=False)[ccol].sum().rename(columns={bcol: "barcode", ccol: "count"})
    else:
        df["_u"] = 1
        agg = df.groupby(bcol, as_index=False)["_u"].sum().rename(columns={bcol: "barcode", "_u": "count"})

    # Attach first non-null value of text columns per barcode
    for src_col, dest_col in [(pncol, "scanner_name"), (notecol, "scanner_notes")]:
        if src_col and src_col in df.columns:
            first_val = (
                df[[bcol, src_col]]
                .rename(columns={bcol: "barcode", src_col: dest_col})
                .groupby("barcode", as_index=False)
                .agg({dest_col: lambda x: next((v for v in x if pd.notna(v) and str(v).strip()), None)})
            )
            agg = agg.merge(first_val, on="barcode", how="left")
        else:
            agg[dest_col] = None

    return agg


def _fetch_variant_map() -> dict[str, dict]:
    """
    Fetch all product variants from the DB in one query.

    Returns a dict keyed by display_name_norm (uppercase):
      {
        "COOPERS PALE ALE S6": {
          "pack_size": 6,
          "is_master": False,
          "master_norm": "COOPERS PALE ALE S1",   # display_name_norm of the master variant
        },
        "COOPERS PALE ALE S1": {
          "pack_size": 1,
          "is_master": True,
          "master_norm": "COOPERS PALE ALE S1",
        },
        ...
      }

    Products not found in this dict are treated as their own master (multiplier = 1).
    """
    import sys
    from pathlib import Path as _Path
    _project_root = str(_Path(__file__).resolve().parent.parent)
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)
    from db import supabase

    rows: list[dict] = []
    page_size = 1000
    offset = 0
    while True:
        resp = (
            supabase.table("product_variants")
            .select("display_name_norm, pack_size, is_master_variant, master_id")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        batch = resp.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size

    # Build master_id → (master display_name_norm, master pack_size) lookup
    master_by_id: dict[str, tuple[str, int]] = {}
    for row in rows:
        if row["is_master_variant"]:
            master_by_id[row["master_id"]] = (row["display_name_norm"], int(row["pack_size"]))

    # Build final variant map
    variant_map: dict[str, dict] = {}
    for row in rows:
        norm = row["display_name_norm"]
        is_master = bool(row["is_master_variant"])
        master_info = master_by_id.get(row["master_id"])
        master_norm = norm if is_master else (master_info[0] if master_info else None)
        master_pack_size = int(row["pack_size"]) if is_master else (master_info[1] if master_info else 1)

        variant_pack_size = int(row["pack_size"])

        # Compute the multiplier: how many master units does one scan of this variant represent.
        #
        # pack_size in the DB can be stored in two ways depending on how it was seeded:
        #   - Absolute: pack_size = raw units (e.g. C30 stored as 30)
        #   - Relative: pack_size = units of the master (e.g. C30-per-C10 stored as 3)
        #
        # Rule: if pack_size is divisible by master_pack_size (and master_pack_size > 1),
        # the value is absolute → divide to get the relative multiplier.
        # Otherwise the value is already the relative multiplier → use it directly.
        # Masters always have multiplier = 1 (they ARE the unit being counted).
        if is_master:
            multiplier = 1
        elif master_pack_size > 1 and variant_pack_size % master_pack_size == 0:
            multiplier = variant_pack_size // master_pack_size
        else:
            multiplier = variant_pack_size

        variant_map[norm] = {
            "multiplier": multiplier,
            "is_master": is_master,
            "master_norm": master_norm,
        }

    logger.info("Loaded %d product variants from DB.", len(variant_map))
    return variant_map


def _load_products(path: Path, variant_map: dict[str, dict]) -> pd.DataFrame:
    """
    Load the Lightspeed products export and resolve each product to its master
    using the DB variant map.

    Required columns: ProductID, ProductName, Barcode.

    For each product:
    - If found in DB and is_master=True  → master is itself, multiplier = 1
    - If found in DB and is_master=False → master is the master variant,
                                           multiplier = pack_size
    - If not found in DB                → treated as own master, multiplier = 1
      (covers food, non-alcohol, or products not yet in the catalog)
    """
    df = _read_table(path, header="infer")
    df.columns = [str(c).strip() if c is not None else "" for c in df.columns]

    required = ["ProductID", "ProductName", "Barcode"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"Products file must contain exact headers {required}. "
            f"Missing: {missing}. Columns found: {list(df.columns)}"
        )

    out = df[required].copy()
    out["Barcode"] = out["Barcode"].map(_clean_barcode)
    out["ProductID"] = out["ProductID"].astype(str).str.strip()
    out["ProductName"] = out["ProductName"].astype(str).str.strip()

    # Drop rows with empty barcodes
    invalid = out["Barcode"].eq("").sum()
    if invalid:
        logger.warning(
            "Products file contains %d rows with empty/invalid barcodes; they will be ignored.",
            invalid,
        )
        out = out[out["Barcode"] != ""]

    # Reject duplicate barcodes (ambiguous matching)
    dup = out[out["Barcode"].duplicated(keep=False)].sort_values("Barcode")
    if not dup.empty:
        sample = dup.head(10)[["ProductID", "ProductName", "Barcode"]].to_dict(orient="records")
        raise ValueError(
            f"Products file contains duplicate barcodes ({len(dup)} rows). "
            f"Example rows: {sample}"
        )

    # Build display_name_norm → (ProductID, ProductName) lookup from the products file.
    # Used to resolve a master variant's Lightspeed ProductID when the scanned product
    # is a non-master variant.
    norm_to_lightspeed: dict[str, tuple[str, str]] = {}
    for _, r in out.iterrows():
        norm = r["ProductName"].strip().upper()
        norm_to_lightspeed[norm] = (str(r["ProductID"]), str(r["ProductName"]))

    db_hits = 0
    db_misses = 0
    master_not_in_file = 0

    def _resolve(row: pd.Series) -> tuple[str, str, int]:
        """Return (master_product_id, master_product_name, units_per_scan)."""
        name_norm = row["ProductName"].strip().upper()
        info = variant_map.get(name_norm)

        if info is None:
            # Product not in our catalog — treat as its own master (covers food,
            # non-alcohol items, or products not yet added to the DB).
            return row["ProductID"], row["ProductName"], 1

        if info["is_master"]:
            return row["ProductID"], row["ProductName"], 1

        # Non-master: resolve to master variant via Lightspeed product file
        master_norm = info["master_norm"]
        multiplier = info["multiplier"]

        if master_norm and master_norm in norm_to_lightspeed:
            master_pid, master_pname = norm_to_lightspeed[master_norm]
            return master_pid, master_pname, multiplier

        # Master variant exists in DB but isn't in the products file.
        # This can happen when Lightspeed's export is filtered/incomplete.
        # Fall back to using this product itself as its own master with the
        # correct multiplier so the unit count is still right.
        logger.warning(
            "Master '%s' for '%s' not found in products file; "
            "using variant itself as master (multiplier=%d).",
            master_norm,
            row["ProductName"],
            multiplier,
        )
        return row["ProductID"], row["ProductName"], multiplier

    results = out.apply(_resolve, axis=1, result_type="expand")
    results.columns = ["_master_product_id", "_master_product_name", "_units_per_scan"]
    out = pd.concat([out, results], axis=1)

    out["_units_per_scan"] = pd.to_numeric(out["_units_per_scan"], errors="coerce")

    return out.reset_index(drop=True)


def _match(scans: pd.DataFrame, products: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    # Exact match: barcode -> product row (includes master + multiplier info)
    exact = scans.merge(products, left_on="barcode", right_on="Barcode", how="left")

    matched_rows: list[dict] = []
    unmatched_rows: list[dict] = []

    for _, r in exact.iterrows():
        b = str(r.get("barcode", "")).strip()
        raw_count = pd.to_numeric(r.get("count", 0), errors="coerce")
        c = 0 if pd.isna(raw_count) else int(raw_count)

        scanner_name = str(r.get("scanner_name", "") or "").strip()
        scanner_notes = str(r.get("scanner_notes", "") or "").strip()

        if not r.get("ProductID") or pd.isna(r.get("ProductID")):
            unmatched_rows.append(
                {
                    "scanned_barcode": b,
                    "count": c,
                    "reason": "unknown_barcode",
                    "scanner_name": scanner_name,
                    "scanner_notes": scanner_notes,
                }
            )
            continue

        master_id = r.get("_master_product_id")
        master_name = r.get("_master_product_name")
        units_per = r.get("_units_per_scan")

        if pd.isna(master_id) or pd.isna(units_per) or int(units_per) <= 0:
            unmatched_rows.append(
                {
                    "scanned_barcode": b,
                    "count": c,
                    "reason": "cannot_convert_missing_unit_variant",
                    "matched_product_name": str(r.get("ProductName", "")).strip(),
                    "scanner_name": scanner_name,
                    "scanner_notes": scanner_notes,
                }
            )
            continue

        units_per_i = int(units_per)
        matched_rows.append(
            {
                "ProductID": str(master_id).strip(),
                "ProductName": str(master_name).strip(),
                "count": c * units_per_i,
            }
        )

    matched = (
        pd.DataFrame(matched_rows)
        if matched_rows
        else pd.DataFrame(columns=["ProductID", "ProductName", "count"])
    )
    unmatched = (
        pd.DataFrame(unmatched_rows)
        if unmatched_rows
        else pd.DataFrame(columns=["scanned_barcode", "count", "reason", "scanner_name", "scanner_notes"])
    )

    if not matched.empty:
        matched["count"] = pd.to_numeric(matched["count"], errors="coerce").fillna(0).astype(int)
        matched = matched.groupby(["ProductID", "ProductName"], as_index=False)["count"].sum()
        matched = matched.sort_values(["ProductName", "ProductID"])

    return matched, unmatched


def run_stocktake_many(scanner_paths: list[Path], products_path: Path, outdir: Path) -> tuple[Path, Path | None]:
    outdir.mkdir(parents=True, exist_ok=True)

    scans_list: list[pd.DataFrame] = []
    for p in scanner_paths:
        if p is None or not str(p).strip():
            continue
        scan_df = _load_scanner(p)
        for col in ("scanner_name", "scanner_notes"):
            if col not in scan_df.columns:
                scan_df[col] = None
        scans_list.append(scan_df)

    if not scans_list:
        raise ValueError("No scanner files provided.")

    scans = pd.concat(scans_list, ignore_index=True)
    # Sum counts per barcode; preserve first non-null text columns across files
    scans = scans.groupby("barcode", as_index=False).agg(
        count=("count", "sum"),
        scanner_name=("scanner_name", lambda x: next((v for v in x if pd.notna(v) and str(v).strip()), None)),
        scanner_notes=("scanner_notes", lambda x: next((v for v in x if pd.notna(v) and str(v).strip()), None)),
    )

    # Load variant map from DB once, then use it for product resolution
    variant_map = _fetch_variant_map()
    products = _load_products(products_path, variant_map)

    matched, unmatched = _match(scans, products)

    final_out = outdir / "final_count.csv"
    matched = matched.rename(columns={"ProductID": "id", "ProductName": "name"})
    matched.to_csv(final_out, index=False)

    unmatched_out: Path | None = None
    if not unmatched.empty:
        unmatched_out = outdir / "unmatched_barcodes.xlsx"
        unmatched = unmatched.copy()
        unmatched["scanned_barcode"] = unmatched["scanned_barcode"].astype(str)
        unmatched["count"] = pd.to_numeric(unmatched["count"], errors="coerce").fillna(0).astype(int)
        unmatched.to_excel(unmatched_out, index=False)

    return final_out, unmatched_out


def run_stocktake(scanner1: Path, scanner2: Path | None, products_path: Path, outdir: Path) -> tuple[Path, Path | None]:
    """Backward-compatible wrapper for the old (scanner1/scanner2) signature."""
    paths = [scanner1]
    if scanner2 is not None and str(scanner2).strip():
        paths.append(scanner2)
    return run_stocktake_many(paths, products_path, outdir)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args()

    scanner_paths = [Path(p) for p in args.scanners]
    pr = Path(args.products)
    outdir = Path(args.outdir)

    final_out, unmatched_out = run_stocktake_many(scanner_paths, pr, outdir)

    # Stable outputs for Streamlit/subprocess parsing (stdout)
    print(f"STOCKTAKE_FINAL_COUNT_PATH={final_out}")
    if unmatched_out:
        print(f"STOCKTAKE_UNMATCHED_PATH={unmatched_out}")

    logger.info("Stocktake output written: %s", final_out)
    if unmatched_out:
        logger.info("Unmatched barcodes written: %s", unmatched_out)
    else:
        logger.info("No unmatched barcodes.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
