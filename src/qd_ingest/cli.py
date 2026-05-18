"""qd-ingest CLI — sub-commands per source."""

from __future__ import annotations

import click


@click.group()
def main() -> None:
    """QUANTDATA ingest pipeline."""


# --- TEJ ---------------------------------------------------------------

@main.command("tej-stock")
@click.option("--csv", type=click.Path(exists=True, dir_okay=False), required=True,
              help="TEJ TWN_EWPRCD CSV file")
@click.option("--year", type=int, multiple=True, help="Restrict to specific years (default: all)")
@click.option("--dry-run", is_flag=True, help="Validate only, no silver write")
def tej_stock(csv: str, year: tuple[int, ...], dry_run: bool) -> None:
    """TEJ stock daily prices -> silver/bars/bars_1d (asset_class=tw_stock)."""
    from .sources.tej import ingest_stock_daily

    ingest_stock_daily(csv_path=csv, years=year or None, dry_run=dry_run)


@main.command("tej-inst-stock")
@click.option("--csv", type=click.Path(exists=True, dir_okay=False), required=True,
              help="TEJ TWN_EWTINST1 三大法人 CSV file")
@click.option("--year", type=int, multiple=True, help="Restrict to specific years (default: all)")
@click.option("--dry-run", is_flag=True)
def tej_inst_stock(csv: str, year: tuple[int, ...], dry_run: bool) -> None:
    """TEJ stock 三大法人 -> silver/flows/tw_inst_stock_daily."""
    from .sources.tej import ingest_inst_stock_daily

    ingest_inst_stock_daily(csv_path=csv, years=year or None, dry_run=dry_run)


@main.command("tej-margin")
@click.option("--csv", type=click.Path(exists=True, dir_okay=False), required=True,
              help="TEJ TWN_EWGIN 融資融券 CSV file")
@click.option("--year", type=int, multiple=True, help="Restrict to specific years (default: all)")
@click.option("--dry-run", is_flag=True)
def tej_margin(csv: str, year: tuple[int, ...], dry_run: bool) -> None:
    """TEJ 融資融券 -> silver/flows/tw_margin_daily."""
    from .sources.tej import ingest_margin_daily

    ingest_margin_daily(csv_path=csv, years=year or None, dry_run=dry_run)


@main.command("tej-fundamentals")
@click.option("--quarterly", type=click.Path(exists=True, dir_okay=False), required=True,
              help="TEJ TWN_EWIFINQ 單季財報 CSV (period_type=Q)")
@click.option("--ytd", type=click.Path(exists=True, dir_okay=False), required=False,
              help="TEJ TWN_EWIFINQ 累季財報 CSV (period_type=YTD)")
@click.option("--dry-run", is_flag=True)
def tej_fundamentals(quarterly: str, ytd: str | None, dry_run: bool) -> None:
    """TEJ 季度財報 -> silver/fundamentals/fin_q."""
    from .sources.tej import ingest_fundamentals_q

    ingest_fundamentals_q(quarterly_csv=quarterly, ytd_csv=ytd, dry_run=dry_run)


# --- TAIFEX ------------------------------------------------------------

@main.command("taifex-inst")
@click.option("--parquet", type=click.Path(exists=True), required=True,
              help="TAIFEX foreign_oi_daily.parquet (already aggregated)")
@click.option("--dry-run", is_flag=True)
def taifex_inst(parquet: str, dry_run: bool) -> None:
    """TAIFEX 期貨三大法人 -> silver/flows/tw_inst_futures_daily."""
    from .sources.taifex import ingest_inst_futures

    ingest_inst_futures(parquet_path=parquet, dry_run=dry_run)


# --- TW futures (MXF / continuous / stock futures) ---------------------

@main.command("mxf")
@click.option("--dry-run", is_flag=True)
def mxf(dry_run: bool) -> None:
    """MXF 1m + 1d cleaned parquets -> silver/bars/bars_{1m,1d}.

    Reads RAW_SOURCES/MXF_1m_clean_all.parquet and MXF_1d_clean_all.parquet.
    """
    from .sources.tw_futures import ingest_mxf

    ingest_mxf(dry_run=dry_run)


@main.command("continuous")
@click.option("--dry-run", is_flag=True)
def continuous(dry_run: bool) -> None:
    """TX / MTX continuous futures -> gold/continuous/{tx,mtx}_continuous_d.parquet.

    Reads RAW_SOURCES/日k 期貨tquant lab/{TX,MTX}_continuous_adj_back.parquet.
    """
    from .sources.tw_futures import ingest_tw_futures_continuous

    ingest_tw_futures_continuous(dry_run=dry_run)


@main.command("stock-futures")
@click.option("--dry-run", is_flag=True)
def stock_futures(dry_run: bool) -> None:
    """Stock futures daily + continuous near-month.

    -> silver/bars/bars_1d (asset_class=tw_stock_futures)
    -> gold/continuous/stock_futures_continuous_d.parquet
    Reads RAW_SOURCES/股票期貨/.
    """
    from .sources.tw_futures import ingest_stock_futures

    ingest_stock_futures(dry_run=dry_run)


# --- Catalog -----------------------------------------------------------

@main.command("build-catalog")
@click.option("--db-path", type=click.Path(dir_okay=False), default=None,
              help="Override target duckdb file (default: catalog/quant.duckdb). "
                   "Use a staging path if the main catalog is locked by a UI session.")
def build_catalog(db_path: str | None) -> None:
    """(Re)build DuckDB catalog with views over silver/gold."""
    from .common.catalog import build

    build(db_path=db_path) if db_path else build()


if __name__ == "__main__":
    main()
