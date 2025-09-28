import pandas as pd
import os
import re
from rich import print
from rich.console import Console
from rich.prompt import Prompt, IntPrompt
from rich.panel import Panel

console = Console()

def load_file(path):
    ext = os.path.splitext(path)[-1].lower()
    if ext in ['.csv']:
        return pd.read_csv(path)
    elif ext in ['.xlsx', '.xls']:
        return pd.read_excel(path)
    else:
        raise ValueError("Unsupported file type.")

def normalize(col):
    # Remove non-alphanumeric and lowercase
    return re.sub(r'\W+', '', col.lower())

def auto_column_mapping(df1_cols, df2_cols):
    # Map df1 columns to df2 columns with fuzzy/partial matching
    mapping = {}
    normalized_df2 = {normalize(col): col for col in df2_cols}

    for col1 in df1_cols:
        norm1 = normalize(col1)
        # First try exact normalized match
        if norm1 in normalized_df2:
            mapping[col1] = normalized_df2[norm1]
            continue
        # Try partial fuzzy matching: find df2 col containing all parts of df1 col (split by space)
        parts = col1.lower().split()
        best_match = None
        best_score = 0
        for col2 in df2_cols:
            col2_low = col2.lower()
            score = sum(part in col2_low for part in parts)
            # If all parts are in col2 and score > best_score, accept
            if score == len(parts) and score > best_score:
                best_match = col2
                best_score = score
        if best_match:
            mapping[col1] = best_match
    return mapping

def data_validation(df):
    console.print(Panel("[bold yellow]Data Validation Mode[/bold yellow]"))
    report_rows = []

    null_summary = df.isnull().sum()
    null_cols = null_summary[null_summary > 0].index.tolist()
    if null_cols:
        console.print(f"[red]Columns with NULL values: {null_cols}[/red]")
    else:
        console.print("[green]No NULL values detected.[/green]")

    special_char_pattern = re.compile(r'[^\w\s\.\-]')
    special_char_issues = []
    for col in df.select_dtypes(include=['object']).columns:
        mask = df[col].astype(str).str.contains(special_char_pattern)
        if mask.any():
            count = mask.sum()
            special_char_issues.append((col, count))
    if special_char_issues:
        console.print("[red]Columns with special characters (count):[/red]")
        for col, cnt in special_char_issues:
            console.print(f" - {col}: {cnt} cells")
    else:
        console.print("[green]No special characters found in string columns.[/green]")

    negative_issues = []
    for col in df.select_dtypes(include=['number']).columns:
        mask = df[col] < 0
        if mask.any():
            count = mask.sum()
            negative_issues.append((col, count))
    if negative_issues:
        console.print("[red]Columns with negative values (count):[/red]")
        for col, cnt in negative_issues:
            console.print(f" - {col}: {cnt} cells")
    else:
        console.print("[green]No negative values detected in numeric columns.[/green]")

    for col in null_cols:
        null_rows = df[df[col].isnull()]
        for idx, row in null_rows.iterrows():
            report_rows.append({'Row': idx+2, 'Column': col, 'Issue': 'NULL Value', 'Value': None})

    for col, _ in special_char_issues:
        special_rows = df[df[col].astype(str).str.contains(special_char_pattern)]
        for idx, row in special_rows.iterrows():
            report_rows.append({'Row': idx+2, 'Column': col, 'Issue': 'Special Characters', 'Value': row[col]})

    for col, _ in negative_issues:
        negative_rows = df[df[col] < 0]
        for idx, row in negative_rows.iterrows():
            report_rows.append({'Row': idx+2, 'Column': col, 'Issue': 'Negative Value', 'Value': row[col]})

    if report_rows:
        report_df = pd.DataFrame(report_rows)
        report_df.to_excel("data_validation_report.xlsx", index=False)
        console.print("[green]📄 Data validation report saved as 'data_validation_report.xlsx'[/green]")
    else:
        console.print("[green]No issues detected. No report generated.[/green]")

def cell_by_cell_comparison(df1, df2, mapping, unique_cols):
    console.print(Panel("[bold blue]Cell-by-Cell Comparison[/bold blue]"))
    df1_renamed = df1.rename(columns=mapping)
    df2_renamed = df2.rename(columns=mapping)

    for col in unique_cols:
        if col not in df1_renamed.columns or col not in df2_renamed.columns:
            raise ValueError(f"Unique key column '{col}' missing after mapping.")

    merged = df1_renamed.merge(df2_renamed, on=unique_cols, how='outer', suffixes=('_f1', '_f2'), indicator=True)

    diff_cols = [col for col in df1_renamed.columns if col not in unique_cols]
    diffs = []

    for col in diff_cols:
        col_f1 = f"{col}_f1"
        col_f2 = f"{col}_f2"
        if col_f1 in merged.columns and col_f2 in merged.columns:
            merged[f"{col}_diff"] = merged[col_f1] != merged[col_f2]
            diffs.append(f"{col}_diff")

    differences = merged[merged[diffs].any(axis=1)]

    if differences.empty:
        console.print("[green]✅ No cell-level differences found![/green]")
        return None

    console.print(f"[red]❗ Differences found: {len(differences)}[/red]")
    console.print("[green]📄 Cell-level difference report saved as 'cell_by_cell_differences.xlsx'[/green]")

    # Prepare detailed report
    report_rows = []
    for _, row in differences.iterrows():
        key_vals = {col: row[col] for col in unique_cols}
        for col in diff_cols:
            val1 = row.get(f"{col}_f1", "")
            val2 = row.get(f"{col}_f2", "")
            if val1 != val2:
                report_rows.append({
                    **key_vals,
                    'Column': col,
                    'File1_Value': val1,
                    'File2_Value': val2
                })

    report_df = pd.DataFrame(report_rows)
    try:
        report_df.to_excel("cell_by_cell_differences.xlsx", index=False)
    except Exception as e:
        console.print(f"[red]Failed to save report: {e}[/red]")

    return report_df

def row_by_row_comparison(df1, df2, mapping):
    console.print(Panel("[bold blue]Row-by-Row Comparison[/bold blue]"))
    df1_renamed = df1.rename(columns=mapping)
    df2_renamed = df2.rename(columns=mapping)

    df1_renamed['row_signature'] = df1_renamed.astype(str).agg('|'.join, axis=1)
    df2_renamed['row_signature'] = df2_renamed.astype(str).agg('|'.join, axis=1)

    missing_in_df2 = df1_renamed[~df1_renamed['row_signature'].isin(df2_renamed['row_signature'])].drop(columns=['row_signature'])
    extra_in_df2 = df2_renamed[~df2_renamed['row_signature'].isin(df1_renamed['row_signature'])].drop(columns=['row_signature'])

    console.print(f"[red]❌ Rows in File 1 missing from File 2: {len(missing_in_df2)}[/red]")
    console.print(f"[red]❌ Extra rows in File 2 not in File 1: {len(extra_in_df2)}[/red]")

    if not missing_in_df2.empty:
        missing_in_df2.to_csv("missing_in_file2.csv", index=False)
        console.print("[green]📄 Saved: missing_in_file2.csv[/green]")
    if not extra_in_df2.empty:
        extra_in_df2.to_csv("extra_in_file2.csv", index=False)
        console.print("[green]📄 Saved: extra_in_file2.csv[/green]")

    return missing_in_df2, extra_in_df2

def main():
    console.print(Panel("[bold magenta]📊 DATA COMPARISON & VALIDATION TOOL[/bold magenta]", expand=False))

    console.print("\nSelect Mode:")
    console.print("[green]1.[/green] Data Validation (Nulls, Special Characters, Negative Values)")
    console.print("[green]2.[/green] Data Comparison")

    mode = IntPrompt.ask("Choose mode", choices=["1", "2"])

    if mode == 1:
        file1_path = Prompt.ask("Enter path to File 1 for Validation")
        try:
            df1 = load_file(file1_path)
        except Exception as e:
            console.print(f"[red]Failed to load file: {e}[/red]")
            return
        data_validation(df1)

    elif mode == 2:
        file1_path = Prompt.ask("Enter path to File 1")
        file2_path = Prompt.ask("Enter path to File 2")
        try:
            df1 = load_file(file1_path)
            df2 = load_file(file2_path)
        except Exception as e:
            console.print(f"[red]Failed to load files: {e}[/red]")
            return

        mapping = auto_column_mapping(df1.columns, df2.columns)
        if mapping:
            console.print("[green]Auto column mapping detected:[/green]")
            for k, v in mapping.items():
                console.print(f" - {k} => {v}")
        else:
            console.print("[yellow]No automatic column mapping found. Columns will be matched as-is.[/yellow]")

        console.print("\nSelect Comparison Type:")
        console.print("[green]1.[/green] Cell-by-Cell Comparison (requires unique keys)")
        console.print("[green]2.[/green] Row-by-Row Comparison (no keys needed)")

        comp_type = IntPrompt.ask("Choose comparison type", choices=["1", "2"])

        if comp_type == 1:
            unique_key_input = Prompt.ask("Enter unique key columns (comma-separated, use File 1 column names)")
            unique_cols = [col.strip() for col in unique_key_input.split(',') if col.strip()]
            if not unique_cols:
                console.print("[red]You must enter at least one unique key column.[/red]")
                return
            try:
                cell_by_cell_comparison(df1, df2, mapping, unique_cols)
            except Exception as e:
                console.print(f"[red]Error during cell-by-cell comparison: {e}[/red]")

        elif comp_type == 2:
            try:
                row_by_row_comparison(df1, df2, mapping)
            except Exception as e:
                console.print(f"[red]Error during row-by-row comparison: {e}[/red]")

if __name__ == "__main__":
    main()
