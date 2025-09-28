import os
import re
import time
import pandas as pd
from collections import defaultdict
from rich import print
from rich.console import Console
from rich.prompt import Prompt, IntPrompt
from rich.panel import Panel
from openpyxl import load_workbook
from openpyxl.styles import Font, Border, Side

# Initialize Rich Console
console = Console()

# ---------- FILE LOADING ----------
def load_file(path):
    ext = os.path.splitext(path)[-1].lower()
    if ext == '.csv':
        return pd.read_csv(path)
    elif ext in ['.xlsx', '.xls']:
        return pd.read_excel(path)
    else:
        raise ValueError("Unsupported file type. Use .csv or .xlsx")

# ---------- COLUMN MAPPING ----------
def get_column_mapping(df1, df2):
    console.print("\n[bold blue]📌 Column Mapping Setup[/bold blue]")
    console.print(f"[blue]File 1 Columns:[/blue] {list(df1.columns)}")
    console.print(f"[blue]File 2 Columns:[/blue] {list(df2.columns)}")

    mapping = {}
    for col1 in df1.columns:
        match = Prompt.ask(f"🔗 Match for [bold]{col1}[/bold] in File 2 (leave blank to skip)", default="")
        if match and match in df2.columns:
            mapping[col1] = match
        elif match:
            console.print(f"[red]⚠️ '{match}' not found in File 2. Skipped.[/red]")
    return mapping

# ---------- DATA VALIDATION ----------
def identify_null_columns(df):
    return df.columns[df.isnull().any()].tolist()

def identify_negative_columns(df):
    return [col for col in df.select_dtypes(include=[int, float]).columns if (df[col] < 0).any()]

def identify_special_characters(df):
    pattern = re.compile(r'[^a-zA-Z0-9.\- ]')
    special_chars = defaultdict(set)
    for col in df.columns:
        for val in df[col].astype(str):
            chars = pattern.findall(val)
            if chars:
                special_chars[col].update(chars)
    return special_chars

def get_rows_with_special_characters(df):
    pattern = re.compile(r'[^a-zA-Z0-9.\- ]')
    return df[df.apply(lambda row: row.astype(str).apply(lambda x: bool(pattern.search(x))).any(), axis=1)]

# ---------- EXCEL UTILS ----------
def auto_adjust_column_width(ws):
    for col in ws.columns:
        max_len = max((len(str(cell.value)) for cell in col if cell.value), default=0)
        ws.column_dimensions[col[0].column_letter].width = max_len + 2

def apply_borders(ws):
    thin = Side(border_style="thin", color="000000")
    for row in ws.iter_rows():
        for cell in row:
            cell.border = Border(top=thin, left=thin, right=thin, bottom=thin)

# ---------- ROW-LEVEL COMPARISON ----------
def row_comparison(df1, df2, mapping):
    console.print("\n[bold blue]🔍 Performing Row-by-Row Comparison[/bold blue]")
    df1 = df1.rename(columns=mapping)
    df2 = df2.rename(columns=mapping)

    df1['row_signature'] = df1.astype(str).agg('|'.join, axis=1)
    df2['row_signature'] = df2.astype(str).agg('|'.join, axis=1)

    missing = df1[~df1['row_signature'].isin(df2['row_signature'])].drop(columns='row_signature')
    extra = df2[~df2['row_signature'].isin(df1['row_signature'])].drop(columns='row_signature')

    if not missing.empty:
        missing.to_csv("missing_in_file2.csv", index=False)
        console.print("[green]📄 Saved: missing_in_file2.csv[/green]")
    if not extra.empty:
        extra.to_csv("extra_in_file2.csv", index=False)
        console.print("[green]📄 Saved: extra_in_file2.csv[/green]")

# ---------- CELL-LEVEL COMPARISON ----------
def cell_by_cell_comparison(df1, df2, mapping, keys):
    console.print("\n[bold blue]🔍 Performing Cell-by-Cell Comparison[/bold blue]")
    df1 = df1.rename(columns=mapping)
    df2 = df2.rename(columns=mapping)

    for col in keys:
        if col not in df1.columns or col not in df2.columns:
            raise ValueError(f"Missing key column: {col}")

    merged = df1.merge(df2, on=keys, how='outer', suffixes=('_f1', '_f2'), indicator=True)

    diff_cols = [col for col in df1.columns if col not in keys]
    diffs = []

    for col in diff_cols:
        col_f1 = f"{col}_f1"
        col_f2 = f"{col}_f2"
        if col_f1 in merged and col_f2 in merged:
            merged[f"{col}_diff"] = merged[col_f1] != merged[col_f2]
            diffs.append(f"{col}_diff")

    differences = merged[merged[diffs].any(axis=1)]

    if differences.empty:
        console.print("[green]✅ No cell-level differences found![/green]")
        return

    # Prepare report
    report_rows = []
    for _, row in differences.iterrows():
        base = {k: row[k] for k in keys}
        for col in diff_cols:
            v1 = row.get(f"{col}_f1")
            v2 = row.get(f"{col}_f2")
            if v1 != v2:
                report_rows.append({
                    **base,
                    'Column': col,
                    'File1_Value': v1,
                    'File2_Value': v2
                })

    pd.DataFrame(report_rows).to_excel("cell_by_cell_differences.xlsx", index=False)
    console.print("[green]📄 Saved: cell_by_cell_differences.xlsx[/green]")

# ---------- VALIDATION EXPORT ----------
def export_validation_issues(df1, df2):
    with pd.ExcelWriter("data_validation_report.xlsx", engine='openpyxl') as writer:
        def save_and_style(df, sheet_name):
            df.to_excel(writer, sheet_name=sheet_name, index=False)
            ws = writer.book[sheet_name]
            auto_adjust_column_width(ws)
            apply_borders(ws)

        # Nulls
        nulls1 = df1[df1.isnull().any(axis=1)]
        nulls2 = df2[df2.isnull().any(axis=1)]
        if not nulls1.empty:
            save_and_style(nulls1, "Nulls_File1")
        if not nulls2.empty:
            save_and_style(nulls2, "Nulls_File2")

        # Negatives
        neg1 = df1[df1.select_dtypes(include=[int, float]).lt(0).any(axis=1)]
        neg2 = df2[df2.select_dtypes(include=[int, float]).lt(0).any(axis=1)]
        if not neg1.empty:
            save_and_style(neg1, "Negatives_File1")
        if not neg2.empty:
            save_and_style(neg2, "Negatives_File2")

        # Special Characters
        sp1 = get_rows_with_special_characters(df1)
        sp2 = get_rows_with_special_characters(df2)
        if not sp1.empty:
            save_and_style(sp1, "SpecialChars_File1")
        if not sp2.empty:
            save_and_style(sp2, "SpecialChars_File2")

    console.print("[green]✅ Data validation report saved as 'data_validation_report.xlsx'[/green]")

# ---------- MAIN ----------
def main():
    console.print(Panel("[bold cyan]📊 Multi-Dimensional Data Validator & Comparator[/bold cyan]", expand=False))

    # Load files
    file1 = Prompt.ask("[blue]Enter path to File 1[/blue]")
    file2 = Prompt.ask("[blue]Enter path to File 2[/blue]")

    try:
        df1 = load_file(file1)
        df2 = load_file(file2)
        console.print("[green]✔ Files loaded successfully[/green]")
    except Exception as e:
        console.print(f"[red]❌ Error loading files: {e}[/red]")
        return

    # Menu
    console.print("\n[bold yellow]Select Operation:[/bold yellow]")
    console.print("1. Row Count Comparison")
    console.print("2. Data Validation Report")
    console.print("3. Row-by-Row Comparison")
    console.print("4. Cell-by-Cell Comparison")
    choice = IntPrompt.ask("Enter your choice", choices=["1", "2", "3", "4"])

    # Column mapping
    mapping = get_column_mapping(df1, df2)

    if choice == 1:
        console.print(f"[green]File 1 rows:[/green] {len(df1)}")
        console.print(f"[green]File 2 rows:[/green] {len(df2)}")
    elif choice == 2:
        export_validation_issues(df1, df2)
    elif choice == 3:
        row_comparison(df1, df2, mapping)
    elif choice == 4:
        keys = Prompt.ask("Enter unique key columns (comma-separated)").split(',')
        keys = [k.strip() for k in keys if k.strip()]
        try:
            cell_by_cell_comparison(df1, df2, mapping, keys)
        except Exception as e:
            console.print(f"[red]❌ Error: {e}[/red]")

if __name__ == "__main__":
    main()
