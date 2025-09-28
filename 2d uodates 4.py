import pandas as pd
import os
from rich import print
from rich.console import Console
from rich.prompt import Prompt, IntPrompt
from rich.panel import Panel
from rich.table import Table

console = Console()

def load_file(path):
    ext = os.path.splitext(path)[-1].lower()
    if ext in ['.csv']:
        return pd.read_csv(path)
    elif ext in ['.xlsx', '.xls']:
        return pd.read_excel(path)
    else:
        raise ValueError("Unsupported file type.")

def preprocess_col_values(series):
    vals = series.dropna().astype(str).str.lower().str.strip()
    vals = vals[vals != '']
    return set(vals.unique())

def map_columns_by_data(df1, df2, threshold=0.3):
    """
    Map columns from df1 to df2 based on Jaccard similarity of unique column values.
    """
    mapping = {}
    used_cols2 = set()

    data1 = {col: preprocess_col_values(df1[col]) for col in df1.columns}
    data2 = {col: preprocess_col_values(df2[col]) for col in df2.columns}

    for col1, vals1 in data1.items():
        best_match = None
        best_score = 0
        for col2, vals2 in data2.items():
            if col2 in used_cols2:
                continue
            if not vals1 or not vals2:
                continue
            intersection = vals1.intersection(vals2)
            union = vals1.union(vals2)
            jaccard = len(intersection) / len(union)
            if jaccard > best_score:
                best_score = jaccard
                best_match = col2
        if best_score >= threshold:
            mapping[col1] = best_match
            used_cols2.add(best_match)
    return mapping

def cell_by_cell_comparison(df1, df2, mapping, unique_cols):
    console.print("\n[bold blue]🔍 Performing Cell-by-Cell Comparison[/bold blue]")

    # Rename columns in both dfs to mapped names
    df1_renamed = df1.rename(columns=mapping)
    df2_renamed = df2.rename(columns=mapping)

    # Check unique keys exist
    for col in unique_cols:
        if col not in df1_renamed.columns or col not in df2_renamed.columns:
            raise ValueError(f"Unique key column '{col}' missing after mapping.")

    merged = df1_renamed.merge(df2_renamed, on=unique_cols, how='outer', suffixes=('_f1', '_f2'), indicator=True)

    compare_cols = [col for col in df1_renamed.columns if col not in unique_cols]

    diffs = []
    for col in compare_cols:
        c1 = f"{col}_f1"
        c2 = f"{col}_f2"
        if c1 in merged.columns and c2 in merged.columns:
            merged[f"{col}_diff"] = merged[c1] != merged[c2]
            diffs.append(f"{col}_diff")

    if not diffs:
        console.print("[green]✅ No comparable columns found for cell-by-cell difference.[/green]")
        return pd.DataFrame()

    differences = merged[merged[diffs].any(axis=1)]

    if differences.empty:
        console.print("[green]✅ No cell-level differences found![/green]")
        return pd.DataFrame()

    # Prepare report rows
    report_rows = []
    for _, row in differences.iterrows():
        key_data = {col: row[col] for col in unique_cols}
        for col in compare_cols:
            if row.get(f"{col}_diff", False):
                report_rows.append({
                    **key_data,
                    'Column': col,
                    'File1_Value': row.get(f"{col}_f1", ""),
                    'File2_Value': row.get(f"{col}_f2", "")
                })

    report_df = pd.DataFrame(report_rows)
    report_df.to_excel("cell_by_cell_differences.xlsx", index=False)

    console.print(f"[red]❗ Differences found: {len(report_df)}[/red]")
    console.print("[green]📄 Cell-level difference report saved as 'cell_by_cell_differences.xlsx'[/green]")

    return report_df

def row_comparison(df1, df2, mapping):
    console.print("\n[bold blue]🔍 Performing Row-by-Row Comparison[/bold blue]")

    df1_renamed = df1.rename(columns=mapping)
    df2_renamed = df2.rename(columns=mapping)

    df1_renamed['row_signature'] = df1_renamed.astype(str).agg('|'.join, axis=1)
    df2_renamed['row_signature'] = df2_renamed.astype(str).agg('|'.join, axis=1)

    missing_in_df2 = df1_renamed[~df1_renamed['row_signature'].isin(df2_renamed['row_signature'])].drop(columns=['row_signature'])
    extra_in_df2 = df2_renamed[~df2_renamed['row_signature'].isin(df1_renamed['row_signature'])].drop(columns=['row_signature'])

    console.print(f"[red]❌ Rows in File 1 missing from File 2:[/red] {len(missing_in_df2)}")
    console.print(f"[red]❌ Extra rows in File 2 not in File 1:[/red] {len(extra_in_df2)}")

    if not missing_in_df2.empty:
        missing_in_df2.to_csv("missing_in_file2.csv", index=False)
        console.print("[green]📄 Saved: missing_in_file2.csv[/green]")
    if not extra_in_df2.empty:
        extra_in_df2.to_csv("extra_in_file2.csv", index=False)
        console.print("[green]📄 Saved: extra_in_file2.csv[/green]")

    return missing_in_df2, extra_in_df2

def data_validation(df, mapping, check_null=True, check_special=True, check_negative=True):
    console.print("\n[bold blue]🔍 Performing Data Validation[/bold blue]")

    df_renamed = df.rename(columns=mapping)
    problems = []

    # Null check
    if check_null:
        null_counts = df_renamed.isnull().sum()
        null_cols = null_counts[null_counts > 0]
        if not null_cols.empty:
            for col, count in null_cols.items():
                problems.append(f"Column '{col}' has {count} null/missing values.")

    # Special char check (non-alphanumeric and non-space)
    if check_special:
        import re
        special_cols = []
        for col in df_renamed.columns:
            # Only check object dtype columns
            if df_renamed[col].dtype == 'O':
                special_count = df_renamed[col].astype(str).apply(lambda x: bool(re.search(r'[^\w\s]', x))).sum()
                if special_count > 0:
                    problems.append(f"Column '{col}' has {special_count} entries with special characters.")

    # Negative values check (only numeric columns)
    if check_negative:
        num_cols = df_renamed.select_dtypes(include=['number']).columns
        for col in num_cols:
            neg_count = (df_renamed[col] < 0).sum()
            if neg_count > 0:
                problems.append(f"Column '{col}' has {neg_count} negative values.")

    if problems:
        console.print("[red]⚠️ Data validation issues found:[/red]")
        for p in problems:
            console.print(f" - {p}")
    else:
        console.print("[green]✅ No data validation issues found![/green]")

def main():
    console.print(Panel("[bold blue]📊 DATA COMPARISON TOOL[/bold blue]", expand=False))

    file1_path = Prompt.ask("[blue]Enter path to File 1[/blue]")
    file2_path = Prompt.ask("[blue]Enter path to File 2[/blue]")

    try:
        df1 = load_file(file1_path)
        df2 = load_file(file2_path)
    except Exception as e:
        console.print(f"[red]❌ Failed to load files: {e}[/red]")
        return

    console.print("\n[blue]Automatically mapping columns by data similarity...[/blue]")
    mapping = map_columns_by_data(df1, df2, threshold=0.3)

    if not mapping:
        console.print("[red]⚠️ No columns mapped automatically. Please check your files.[/red]")
        return

    console.print("\n[green]📌 Column mapping result:[/green]")
    for k, v in mapping.items():
        console.print(f" - [bold]{k}[/bold]  ➡️  [bold]{v}[/bold]")

    console.print("\n[blue]Choose Operation:[/blue]")
    console.print("[green]1.[/green] Cell-by-cell comparison (requires unique key columns)")
    console.print("[green]2.[/green] Row-by-row comparison")
    console.print("[green]3.[/green] Data validation (nulls, special chars, negative values) on File 1")
    console.print("[green]4.[/green] Data validation (nulls, special chars, negative values) on File 2")

    choice = IntPrompt.ask("Select [1-4]", choices=["1", "2", "3", "4"])

    if choice == 1:
        unique_key_input = Prompt.ask("[blue]Enter unique key columns (comma-separated, File 1 column names)[/blue]")
        unique_cols = [c.strip() for c in unique_key_input.split(',') if c.strip()]
        try:
            _ = cell_by_cell_comparison(df1, df2, mapping, unique_cols)
        except Exception as e:
            console.print(f"[red]❌ Error: {e}[/red]")

    elif choice == 2:
        try:
            _missing, _extra = row_comparison(df1, df2, mapping)
        except Exception as e:
            console.print(f"[red]❌ Error: {e}[/red]")

    elif choice == 3:
        data_validation(df1, mapping)

    elif choice == 4:
        data_validation(df2, {v:k for k,v in mapping.items()})  # Reverse mapping for File 2

if __name__ == "__main__":
    main()
