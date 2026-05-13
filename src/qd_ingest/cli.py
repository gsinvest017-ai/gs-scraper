"""qd-ingest CLI — sub-commands per source."""

from __future__ import annotations

import click


@click.group()
def main() -> None:
    """QUANTDATA ingest pipeline."""


@main.command()
@click.option("--csv", type=click.Path(exists=True, dir_okay=False), required=True,
              help="TEJ TWN_EWPRCD CSV file")
@click.option("--year", type=int, multiple=True, help="Restrict to specific years (default: all)")
@click.option("--dry-run", is_flag=True, help="Validate only, no silver write")
def tej_stock(csv: str, year: tuple[int, ...], dry_run: bool) -> None:
    """TEJ stock daily prices -> silver/bars/bars_1d (asset_class=tw_stock)."""
    from .sources.tej import ingest_stock_daily

    ingest_stock_daily(csv_path=csv, years=year or None, dry_run=dry_run)


@main.command()
@click.option("--parquet", type=click.Path(exists=True), required=True,
              help="TAIFEX foreign_oi_daily.parquet (already aggregated)")
@click.option("--dry-run", is_flag=True)
def taifex_inst(parquet: str, dry_run: bool) -> None:
    """TAIFEX three-institution futures flow -> silver/flows/tw_inst_futures_daily."""
    from .sources.taifex import ingest_inst_futures

    ingest_inst_futures(parquet_path=parquet, dry_run=dry_run)


@main.command()
def build_catalog() -> None:
    """(Re)build catalog/quant.duckdb with views over silver/gold."""
    from .common.catalog import build

    build()


if __name__ == "__main__":
    main()
