import time  # Add this at the top of your file

def main():
    console.print(Panel("[bold cyan]🧠 Data Validation and Comparison Tool[/bold cyan]"))

    path1 = Prompt.ask("[bold]📂 Enter path to File 1[/bold]")
    path2 = Prompt.ask("[bold]📂 Enter path to File 2[/bold]")

    # Measure and display time + size for File 1
    start1 = time.perf_counter()
    df1 = load_file(path1)
    end1 = time.perf_counter()
    size1 = os.path.getsize(path1)
    console.print(f"[bold green]✅ File 1 loaded in {end1 - start1:.2f} seconds[/bold green]")
    console.print(f"[bold green]📦 File 1 size: {size1 / 1024:.2f} KB ({size1 / (1024*1024):.2f} MB)[/bold green]")

    # Measure and display time + size for File 2
    start2 = time.perf_counter()
    df2 = load_file(path2)
    end2 = time.perf_counter()
    size2 = os.path.getsize(path2)
    console.print(f"[bold blue]✅ File 2 loaded in {end2 - start2:.2f} seconds[/bold blue]")
    console.print(f"[bold blue]📦 File 2 size: {size2 / 1024:.2f} KB ({size2 / (1024*1024):.2f} MB)[/bold blue]")

    console.print("[bold magenta]\n🔗 Auto Mapping Columns...[/bold magenta]")
    mapping = map_columns_by_alias(df1.columns, df2.columns)

    table = Table(title="Mapped Columns")
    table.add_column("File 1 Column")
    table.add_column("File 2 Column")
    for k, v in mapping.items():
        table.add_row(k, v)
    console.print(table)

    while True:
        console.print("\n[bold cyan]Choose Validation/Comparison Option:[/bold cyan]")
        console.print("1. Count of Records")
        console.print("2. Null Value Records")
        console.print("3. Negative Value Records")
        console.print("4. Special Character Records")
        console.print("5. Row-by-Row Comparison")
        console.print("6. Cell-by-Cell Comparison")
        console.print("7. Exit")

        choice = IntPrompt.ask("Enter your choice", choices=[str(i) for i in range(1, 8)])

        if choice == 1:
            console.print(f"File 1 Rows: {len(df1)}, File 2 Rows: {len(df2)}")
        elif choice == 2:
            perform_null_check(df1, df2)
        elif choice == 3:
            perform_negative_check(df1, df2)
        elif choice == 4:
            perform_special_char_check(df1, df2)
        elif choice == 5:
            df1_m = df1.rename(columns=mapping)
            df2_m = df2.rename(columns=mapping)
            row_comparison(df1_m, df2_m)
        elif choice == 6:
            keys = Prompt.ask("Enter unique key columns (comma-separated)").split(",")
            keys = [k.strip() for k in keys if k.strip()]
            df1_m = df1.rename(columns=mapping)
            df2_m = df2.rename(columns=mapping)
            cell_by_cell_comparison(df1, df2, mapping, keys)
        elif choice == 7:
            console.print("[bold green]✅ Exiting. All done![/bold green]")
            break
