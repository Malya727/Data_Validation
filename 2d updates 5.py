import os
import re
import difflib
import pandas as pd
from rich import print
from rich.console import Console
from rich.prompt import Prompt, IntPrompt
from rich.panel import Panel
from rich.table import Table

console = Console()

# ========== File Loading ==========

def load_file(path):
    ext = os.path.splitext(path)[-1].lower()
    if ext in ('.csv',):
        return pd.read_csv(path)
    elif ext in ('.xlsx', '.xls'):
        return pd.read_excel(path)
    else:
        raise ValueError("Unsupported file type. Use CSV or XLSX/XLS")

# ========== Normalization & Alias Utilities ==========

def normalize(col_name: str) -> str:
    """Normalize a column name (lowercase, remove non-alphanumeric)."""
    return re.sub(r'\W+', '', col_name.lower())

# A base alias map: you can extend this list as needed
ALIAS_MAP = {
    "country": ["ctry", "nation", "cnty"],
    "product": ["sku", "pdct", "item", "prod"],
    "description": ["desc", "descr", "detail"],
    "code": ["cd", "identifier", "id"],
    "costcenter": ["costcenter", "cc"],
    "level1code": ["lvl1code", "l1code", "level1cd"],
    "level2code": ["lvl2code", "l2code", "level2cd"],
    # you can add many more as per your domain
}

def find_alias_match(norm1: str, df2_norm_to_orig: dict):
    """
    Try to match norm1 to a df2 column using alias map.
    Returns original df2 column name if found, else None.
    """
    for master, aliases in ALIAS_MAP.items():
        # if norm1 matches master or one of aliases
        if norm1 == master or norm1 in aliases:
            # try to find df2 column whose normalized is master or alias
            for alias in [master] + aliases:
                if alias in df2_norm_to_orig:
                    return df2_norm_to_orig[alias]
    return None

# ========== Column Mapping ==========

def auto_map_columns(df1: pd.DataFrame, df2: pd.DataFrame):
    """
    Map columns from df1 to df2 using:
      1. Alias map
      2. Fuzzy matching fallback
      3. Substring fallback
    Returns a dict: {col1: col2 or None}
    """
    mapping = {}
    used_in_df2 = set()

    # Prepare map of normalized df2 to original name
    df2_norm_to_orig = {}
    for c2 in df2.columns:
        df2_norm_to_orig[normalize(c2)] = c2

    # 1st pass: alias-based
    for c1 in df1.columns:
        norm1 = normalize(c1)
        alias_match = find_alias_match(norm1, df2_norm_to_orig)
        if alias_match and alias_match not in used_in_df2:
            mapping[c1] = alias_match
            used_in_df2.add(alias_match)
        else:
            mapping[c1] = None

    # 2nd pass: fuzzy matching for unmapped
    unmapped = [c1 for c1, m in mapping.items() if m is None]
    df2_remaining = [c2 for c2 in df2.columns if c2 not in used_in_df2]

    for c1 in unmapped:
        matches = difflib.get_close_matches(c1, df2_remaining, n=1, cutoff=0.6)
        if matches:
            best = matches[0]
            mapping[c1] = best
            used_in_df2.add(best)

    # 3rd pass: substring fallback
    still_unmapped = [c1 for c1, m in mapping.items() if m is None]
    for c1 in still_unmapped:
        parts = re.split(r'\W+', c1.lower())
        for c2 in df2.columns:
            if c2 in used_in_df2:
                continue
            c2_low = c2.lower()
            if all(part and part in c2_low for part in parts):
                mapping[c1] = c2
                used_in_df2.add(c2)
                break

    return mapping

def display_mapping(mapping: dict):
    table = Table(title="Column Mapping: File1 → File2")
    table.add_column("File1 Column", style="cyan")
    table.add_column("Mapped File2 Column", style="magenta")
    for c1, c2 in mapping.items():
        table.add_row(c1, c2 if c2 else "—")
    console.print(table)

# ========== Validation Functions ==========

def validate_nulls(df: pd.DataFrame, mapping: dict, label: str):
    console.print(Panel(f"[yellow]Null / Missing Value Check on {label}[/yellow]"))
    df_ren = df.rename(columns=mapping)
    null_counts = df_ren.isnull().sum()
    null_counts = null_counts[null_counts > 0]
    if null_counts.empty:
        console.print("[green]No nulls found.[/green]")
        return
    tbl = Table(title=f"Columns with Nulls in {label}")
    tbl.add_column("Column")
    tbl.add_column("Null Count", justify="right")
    for c, cnt in null_counts.items():
        tbl.add_row(str(c), str(cnt))
    console.print(tbl)

def validate_special_chars(df: pd.DataFrame, mapping: dict, label: str):
    console.print(Panel(f"[yellow]Special Character Check on {label}[/yellow]"))
    df_ren = df.rename(columns=mapping)
    pattern = re.compile(r'[^\w\s]')
    cols = []
    for c in df_ren.select_dtypes(include=['object']).columns:
        cnt = df_ren[c].astype(str).apply(lambda v: bool(pattern.search(v))).sum()
        if cnt > 0:
            cols.append((c, cnt))
    if not cols:
        console.print("[green]No special characters found.[/green]")
        return
    tbl = Table(title=f"Columns with Special Characters ({label})")
    tbl.add_column("Column")
    tbl.add_column("Count", justify="right")
    for c, cnt in cols:
        tbl.add_row(c, str(cnt))
    console.print(tbl)

def validate_negative(df: pd.DataFrame, mapping: dict, label: str):
    console.print(Panel(f"[yellow]Negative Value Check on {label}[/yellow]"))
    df_ren = df.rename(columns=mapping)
    cols = []
    for c in df_ren.select_dtypes(include=['number']).columns:
        cnt = (df_ren[c] < 0).sum()
        if cnt > 0:
            cols.append((c, cnt))
    if not cols:
        console.print("[green]No negative values found.[/green]")
        return
    tbl = Table(title=f"Columns with Negative Values ({label})")
    tbl.add_column("Column")
    tbl.add_column("Count", justify="right")
    for c, cnt in cols:
        tbl.add_row(c, str(cnt))
    console.print(tbl)

# ========== Comparison Functions ==========

def cell_by_cell_comparison(df1: pd.DataFrame, df2: pd.DataFrame, mapping: dict, unique_keys: list):
    console.print(Panel("[bold blue]🔍 Cell-by-Cell Comparison[/bold blue]"))
    df1r = df1.rename(columns=mapping)
    df2r = df2.rename(columns=mapping)

    for k in unique_keys:
        if k not in df1r.columns or k not in df2r.columns:
            raise ValueError(f"Unique key '{k}' missing after mapping")

    merged = df1r.merge(df2r, on=unique_keys, how='outer', suffixes=('_f1', '_f2'), indicator=True)
    compare_cols = [c for c in df1r.columns if c not in unique_keys]

    diffs = []
    for col in compare_cols:
        c1 = col + "_f1"
        c2 = col + "_f2"
        if c1 in merged.columns and c2 in merged.columns:
            merged[col + "_diff"] = merged[c1] != merged[c2]
            diffs.append(col + "_diff")

    if not diffs:
        console.print("[green]✅ No comparable columns found for cell-level comparison.[/green]")
        return pd.DataFrame()

    diff_rows = merged[merged[diffs].any(axis=1)]
    if diff_rows.empty:
        console.print("[green]✅ No cell-level differences found![/green]")
        return pd.DataFrame()

    report = []
    for _, row in diff_rows.iterrows():
        key_data = {k: row[k] for k in unique_keys}
        for col in compare_cols:
            flag = row.get(col + "_diff", False)
            if flag:
                report.append({
                    **key_data,
                    "Column": col,
                    "Value_File1": row.get(col + "_f1"),
                    "Value_File2": row.get(col + "_f2")
                })

    report_df = pd.DataFrame(report)
    fname = "cell_by_cell_differences.xlsx"
    report_df.to_excel(fname, index=False)
    console.print(f"[red]❗ Differences found: {len(report_df)}[/red]")
    console.print(f"[green]📄 Cell-level difference report saved: {fname}[/green]")
    return report_df

def row_by_row_comparison(df1: pd.DataFrame, df2: pd.DataFrame, mapping: dict):
    console.print(Panel("[bold blue]🔍 Row-by-Row Comparison[/bold blue]"))
    df1r = df1.rename(columns=mapping)
    df2r = df2.rename(columns=mapping)

    df1r['__signature'] = df1r.astype(str).agg('|'.join, axis=1)
    df2r['__signature'] = df2r.astype(str).agg('|'.join, axis=1)

    missing = df1r[~df1r['__signature'].isin(df2r['__signature'])].drop(columns='__signature')
    extra = df2r[~df2r['__signature'].isin(df1r['__signature'])].drop(columns='__signature')

    console.print(f"[red]❌ Rows in File1 missing in File2: {len(missing)}[/red]")
    console.print(f"[red]❌ Rows in File2 extra vs File1: {len(extra)}[/red]")

    if not missing.empty:
        fname1 = "rows_missing_file2.csv"
        missing.to_csv(fname1, index=False)
        console.print(f"[green]📄 Missing rows saved: {fname1}[/green]")
    if not extra.empty:
        fname2 = "rows_extra_file2.csv"
        extra.to_csv(fname2, index=False)
        console.print(f"[green]📄 Extra rows saved: {fname2}[/green]")

    return missing, extra

# ========== Main Function ==========

def main():
    console.print(Panel("[bold magenta]📊 Data Comparison & Validation Tool[/bold magenta]", expand=False))

    file1 = Prompt.ask("Enter path for File 1")
    file2 = Prompt.ask("Enter path for File 2")

    try:
        df1 = load_file(file1)
        df2 = load_file(file2)
    except Exception as e:
        console.print(f"[red]Failed to load files: {e}[/red]")
        return

    console.print("\n[blue]Performing automatic column mapping...[/blue]")
    mapping = auto_map_columns(df1, df2)
    display_mapping(mapping)

    console.print("\n[blue]Choose Operation:[/blue]")
    console.print("[green]1.[/green] Cell-by-cell comparison")
    console.print("[green]2.[/green] Row-by-row comparison")
    console.print("[green]3.[/green] Validate nulls (File1)")
    console.print("[green]4.[/green] Validate special chars (File1)")
    console.print("[green]5.[/green] Validate negative values (File1)")
    console.print("[green]6.[/green] Validate nulls (File2)")
    console.print("[green]7.[/green] Validate special chars (File2)")
    console.print("[green]8.[/green] Validate negative values (File2)")
    console.print("[green]0.[/green] Exit")

    choice = IntPrompt.ask("Select option", choices=[str(i) for i in range(9)])

    if choice == 1:
        unique_input = Prompt.ask("Enter unique key column(s), comma-separated (File1 names)")
        unique_keys = [u.strip() for u in unique_input.split(',') if u.strip()]
        if not unique_keys:
            console.print("[red]You must enter at least one unique key.[/red]")
        else:
            try:
                _ = cell_by_cell_comparison(df1, df2, mapping, unique_keys)
            except Exception as ex:
                console.print(f"[red]Error: {ex}[/red]")

    elif choice == 2:
        try:
            _m, _e = row_by_row_comparison(df1, df2, mapping)
        except Exception as ex:
            console.print(f"[red]Error: {ex}[/red]")

    elif choice == 3:
        validate_nulls(df1, mapping, "File1")
    elif choice == 4:
        validate_special_chars(df1, mapping, "File1")
    elif choice == 5:
        validate_negative(df1, mapping, "File1")

    elif choice == 6:
        # For File2, we need a reverse mapping: map File2 columns back to original for validation
        # But since validation only checks existence in File2, we can use identity mapping
        validate_nulls(df2, {c:c for c in df2.columns}, "File2")
    elif choice == 7:
        validate_special_chars(df2, {c:c for c in df2.columns}, "File2")
    elif choice == 8:
        validate_negative(df2, {c:c for c in df2.columns}, "File2")
    else:
        console.print("[blue]Exiting...[/blue]")

if __name__ == "__main__":
    main()
