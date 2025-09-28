import os
import pandas as pd
import numpy as np
from rich.console import Console
from rich.prompt import Prompt, IntPrompt
from rich.panel import Panel
from rich.table import Table
from sentence_transformers import SentenceTransformer, util

console = Console()

def load_file(path):
    ext = os.path.splitext(path)[-1].lower()
    if ext in ['.csv']:
        return pd.read_csv(path)
    elif ext in ['.xlsx', '.xls']:
        return pd.read_excel(path)
    else:
        raise ValueError("Unsupported file type. Please use CSV or Excel.")

def auto_map_columns(cols1, cols2, model, threshold=0.6):
    """
    Map columns from cols1 to cols2 using semantic similarity via sentence-transformers.
    """
    mapping = {}
    used_cols2 = set()

    embeddings1 = model.encode(cols1, convert_to_tensor=True)
    embeddings2 = model.encode(cols2, convert_to_tensor=True)

    cosine_scores = util.cos_sim(embeddings1, embeddings2).cpu().numpy()

    for i, col1 in enumerate(cols1):
        col_scores = cosine_scores[i]
        sorted_idx = np.argsort(-col_scores)  # descending order
        for idx2 in sorted_idx:
            if col_scores[idx2] < threshold:
                break
            if cols2[idx2] not in used_cols2:
                mapping[col1] = cols2[idx2]
                used_cols2.add(cols2[idx2])
                break
    return mapping

def display_mapping(mapping):
    table = Table(title="Auto Column Mapping")
    table.add_column("File 1 Column", style="cyan", no_wrap=True)
    table.add_column("File 2 Column", style="magenta")
    for k,v in mapping.items():
        table.add_row(k, v)
    console.print(table)

def cell_by_cell_comparison(df1, df2, mapping, unique_cols):
    console.print(Panel("[blue bold]🔍 Cell-by-Cell Comparison[/blue bold]"))
    # Rename columns according to mapping
    df1_renamed = df1.rename(columns=mapping)
    df2_renamed = df2.rename(columns=mapping)

    # Check unique keys exist
    for col in unique_cols:
        if col not in df1_renamed.columns or col not in df2_renamed.columns:
            raise ValueError(f"Unique key column '{col}' missing after mapping.")

    merged = df1_renamed.merge(df2_renamed, on=unique_cols, how='outer', suffixes=('_f1', '_f2'), indicator=True)

    # Columns to compare (exclude unique keys)
    diff_cols = [col for col in df1_renamed.columns if col not in unique_cols]
    diffs = []

    for col in diff_cols:
        col_f1 = f"{col}_f1"
        col_f2 = f"{col}_f2"
        if col_f1 in merged.columns and col_f2 in merged.columns:
            merged[f"{col}_diff"] = merged[col_f1] != merged[col_f2]
            diffs.append(f"{col}_diff")

    if not diffs:
        console.print("[green]✅ No comparable columns found for cell-by-cell difference.[/green]")
        return None

    differences = merged[merged[diffs].any(axis=1)]

    if differences.empty:
        console.print("[green]✅ No cell-level differences found![/green]")
        return None

    # Prepare report rows for difference report file
    report_rows = []
    for _, row in differences.iterrows():
        key_data = {col: row[col] for col in unique_cols}
        for col in diff_cols:
            val1 = row.get(f"{col}_f1", "")
            val2 = row.get(f"{col}_f2", "")
            if val1 != val2:
                report_rows.append({
                    **key_data,
                    'Column': col,
                    'File1_Value': val1,
                    'File2_Value': val2
                })

    report_df = pd.DataFrame(report_rows)
    output_path = "cell_by_cell_differences.xlsx"
    report_df.to_excel(output_path, index=False)
    console.print(f"[green]📄 Cell-level difference report saved as '{output_path}'[/green]")
    return report_df

def row_by_row_comparison(df1, df2, mapping):
    console.print(Panel("[blue bold]🔍 Row-by-Row Comparison[/blue bold]"))
    df1_renamed = df1.rename(columns=mapping)
    df2_renamed = df2.rename(columns=mapping)

    # Create a row signature by concatenating all columns as string
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

def check_nulls(df, file_label):
    console.print(Panel(f"[yellow]Checking for NULL/Missing values in {file_label}[/yellow]"))
    null_counts = df.isnull().sum()
    null_counts = null_counts[null_counts > 0]
    if null_counts.empty:
        console.print("[green]No NULL values found.[/green]")
        return
    table = Table(title=f"NULL values in {file_label}")
    table.add_column("Column", style="cyan")
    table.add_column("NULL Count", justify="right", style="red")
    for col, cnt in null_counts.items():
        table.add_row(str(col), str(cnt))
    console.print(table)

def check_special_characters(df, file_label):
    console.print(Panel(f"[yellow]Checking for Special Characters in {file_label}[/yellow]"))
    special_char_cols = {}
    for col in df.select_dtypes(include=['object']):
        # check if any special char exists in the column values
        special_rows = df[col].astype(str).str.contains(r'[^a-zA-Z0-9\s]', regex=True)
        if special_rows.any():
            special_char_cols[col] = special_rows.sum()
    if not special_char_cols:
        console.print("[green]No special characters found.[/green]")
        return
    table = Table(title=f"Columns with Special Characters in {file_label}")
    table.add_column("Column", style="cyan")
    table.add_column("Rows with Special Chars", justify="right", style="red")
    for col, cnt in special_char_cols.items():
        table.add_row(col, str(cnt))
    console.print(table)

def check_negative_values(df, file_label):
    console.print(Panel(f"[yellow]Checking for Negative Numeric Values in {file_label}[/yellow]"))
    negative_cols = {}
    for col in df.select_dtypes(include=[np.number]):
        if (df[col] < 0).any():
            negative_cols[col] = (df[col] < 0).sum()
    if not negative_cols:
        console.print("[green]No negative values found.[/green]")
        return
    table = Table(title=f"Columns with Negative Values in {file_label}")
    table.add_column("Column", style="cyan")
    table.add_column("Negative Value Count", justify="right", style="red")
    for col, cnt in negative_cols.items():
        table.add_row(col, str(cnt))
    console.print(table)

def main():
    console.print(Panel("[bold blue]📊 DATA COMPARISON TOOL[/bold blue]", expand=False))

    file1_path = Prompt.ask("[blue]Enter path to File 1 (CSV/XLSX)[/blue]")
    file2_path = Prompt.ask("[blue]Enter path to File 2 (CSV/XLSX)[/blue]")

    try:
        df1 = load_file(file1_path)
        df2 = load_file(file2_path)
    except Exception as e:
        console.print(f"[red]❌ Failed to load files: {e}[/red]")
        return

    model = SentenceTransformer('all-MiniLM-L6-v2')  # small & fast model

    console.print(Panel("[blue]Auto Mapping Columns Using Semantic Similarity[/blue]"))
    mapping = auto_map_columns(list(df1.columns), list(df2.columns), model)
    if not mapping:
        console.print("[red]⚠️ No columns were matched automatically. Check file columns.[/red]")
        return

    display_mapping(mapping)

    console.print("\n[blue]Choose Option:[/blue]")
    console.print("[green]1.[/green] Cell-by-cell comparison (requires unique key)")
    console.print("[green]2.[/green] Row-by-row comparison (no key needed)")
    console.print("[green]3.[/green] Check for NULL values")
    console.print("[green]4.[/green] Check for Special Characters")
    console.print("[green]5.[/green] Check for Negative Numeric Values")
    console.print("[green]0.[/green] Exit")

    choice = IntPrompt.ask("Select an option", choices=[str(i) for i in range(6)])

    if choice == 1:
        unique_key_input = Prompt.ask("[blue]Enter unique key columns (comma-separated, File 1 names)[/blue]")
        unique_cols = [col.strip() for col in unique_key_input.split(',') if col.strip()]
        if not unique_cols:
            console.print("[red]❌ You must provide at least one unique key column.[/red]")
            return
        try:
            cell_by_cell_comparison(df1, df2, mapping, unique_cols)
        except Exception as e:
            console.print(f"[red]❌ Error during cell-by-cell comparison: {e}[/red]")

    elif choice == 2:
        try:
            row_by_row_comparison(df1, df2, mapping)
        except Exception as e:
            console.print(f"[red]❌ Error during row-by-row comparison: {e}[/red]")

    elif choice == 3:
        check_nulls(df1, "File 1")
        check_nulls(df2, "File 2")

    elif choice == 4:
        check_special_characters(df1, "File 1")
        check_special_characters(df2, "File 2")

    elif choice == 5:
        check_negative_values(df1, "File 1")
        check_negative_values(df2, "File 2")

    else:
        console.print("[blue]Exiting...[/blue]")

if __name__ == "__main__":
    main()
