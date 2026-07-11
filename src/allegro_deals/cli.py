from __future__ import annotations

import argparse
import os
import sys
import time

from .apify import ApifyError, ApifyFetcher
from .brightdata import BrightDataError, BrightDataFetcher
from .browser import AllegroBrowser, BotBlockedError
from .detect import DEFAULT_MAX_RATIO, classify_cross_market
from .extract import offer_id_from_url, offers_from_html
from .fx import get_pln_czk
from .scanner import append_findings, compare_markets, scan_query


def _log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def _make_fetcher(args: argparse.Namespace):
    engine = args.engine
    if engine == "auto":
        if args.brightdata_token or os.environ.get("BRIGHTDATA_API_TOKEN") or os.environ.get("BRIGHTDATA_TOKEN"):
            engine = "brightdata"
        elif args.apify_token or os.environ.get("APIFY_TOKEN"):
            engine = "apify"
        else:
            engine = "browser"
    if engine == "brightdata":
        return BrightDataFetcher(token=args.brightdata_token, zone=args.brightdata_zone)
    if engine == "apify":
        return ApifyFetcher(token=args.apify_token)
    return AllegroBrowser(headful=args.headful, profile_dir=args.profile)


def _cmd_scan(args: argparse.Namespace) -> int:
    fx = args.fx or get_pln_czk()
    _log(f"PLN/CZK rate: {fx:.3f}")
    all_anomalies = []
    try:
        with _make_fetcher(args) as fetcher:
            for query in args.queries:
                all_anomalies += scan_query(
                    fetcher,
                    query,
                    fx_pln_czk=fx,
                    pages=args.pages,
                    max_products=args.max_products,
                    max_ratio=args.max_ratio,
                    log=_log,
                )
    except (BotBlockedError, ApifyError, BrightDataError) as exc:
        print(f"blocked/failed: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # playwright network errors: keep the message, drop the traceback
        print(f"network/browser error: {exc}", file=sys.stderr)
        return 3
    if all_anomalies:
        append_findings(all_anomalies, args.out)
        swaps = sum(1 for a in all_anomalies if a.kind == "currency_swap")
        print(
            f"\n{len(all_anomalies)} anomalies ({swaps} likely currency swaps) "
            f"appended to {args.out}"
        )
    else:
        print("\nno anomalies found")
    return 0


def _offer_from_html(html: str, offer_id: str | None, currency: str):
    offers = [o for o in offers_from_html(html) if o.currency == currency]
    if offer_id:
        for o in offers:
            if o.offer_id == offer_id:
                return o
    return offers[0] if offers else None


def _pl_reference_by_model(fetcher, title: str, cz_html: str = "") -> tuple[float, str] | None:
    """Median PLN price for the same product found via allegro.pl search.

    Fallback for offers that exist only on allegro.cz (no shared offer id).
    Prefers an EAN search (exact product match), then the model number
    from the title.
    """
    import urllib.parse
    from statistics import median

    from .detect import model_tokens
    from .extract import ean_from_html
    from .scanner import PL_SEARCH_URL

    ean = ean_from_html(cz_html) if cz_html else None
    if ean:
        html = fetcher.get_html(PL_SEARCH_URL.format(query=ean))
        prices = [o.price for o in offers_from_html(html) if o.currency == "PLN"]
        if prices:
            return median(prices), (
                f"median of {len(prices)} allegro.pl offers for EAN {ean}"
            )
    tokens = model_tokens(title)
    if not tokens:
        return None
    token = sorted(tokens)[0]
    query = f"{title.split()[0]} {token}"
    html = fetcher.get_html(PL_SEARCH_URL.format(query=urllib.parse.quote(query)))
    pln_offers = [o for o in offers_from_html(html) if o.currency == "PLN"]
    prices = [o.price for o in pln_offers if token in model_tokens(o.title)]
    if prices:
        return median(prices), f"median of {len(prices)} allegro.pl offers for '{query}'"
    # Polish titles often omit the model number; with such a specific query
    # a handful of results is still a trustworthy reference.
    if 0 < len(pln_offers) <= 5:
        return (
            median(o.price for o in pln_offers),
            f"median of {len(pln_offers)} allegro.pl results for '{query}' "
            "(model number not in titles)",
        )
    return None


def _cmd_check(args: argparse.Namespace) -> int:
    offer_id = args.offer if args.offer.isdigit() else offer_id_from_url(args.offer)
    if not offer_id:
        print(f"could not find an offer id in: {args.offer}", file=sys.stderr)
        return 1
    fx = args.fx or get_pln_czk()
    cz_url = args.offer if args.offer.startswith("http") else f"https://allegro.cz/nabidka/{offer_id}"
    pl_url = f"https://allegro.pl/oferta/{offer_id}"
    try:
        with _make_fetcher(args) as fetcher:
            cz_html = fetcher.get_html(cz_url)
            cz_offer = _offer_from_html(cz_html, offer_id, "CZK")
            if cz_offer is None:
                print(f"could not read the CZK price from {cz_url}", file=sys.stderr)
                return 1
            pln_source = f"same offer ({pl_url})"
            try:
                pl_offer = _offer_from_html(fetcher.get_html(pl_url), offer_id, "PLN")
            except (BotBlockedError, ApifyError, BrightDataError):
                pl_offer = None
            if pl_offer is not None:
                pln = pl_offer.price
            else:
                # offer not mirrored on allegro.pl - compare by model number
                ref = _pl_reference_by_model(fetcher, cz_offer.title, cz_html)
                if ref is None:
                    print(
                        f"offer {offer_id} not found on allegro.pl and no "
                        "model-number match either", file=sys.stderr,
                    )
                    return 1
                pln, pln_source = ref
    except (BotBlockedError, ApifyError, BrightDataError) as exc:
        print(f"blocked/failed: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"network/browser error: {exc}", file=sys.stderr)
        return 3
    czk = cz_offer.price
    verdict = classify_cross_market(czk, pln, fx)
    print(f"offer {offer_id}: {cz_offer.title[:70]}")
    print(f"  {czk:.2f} CZK on allegro.cz vs {pln:.2f} PLN ({pln_source})")
    print(f"  implied rate {czk / pln:.2f} (real {fx:.2f}) -> {verdict}")
    if verdict == "currency_swap":
        print("  looks like the PLN price is displayed as CZK - that's a deal!")
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    fx = args.fx or get_pln_czk()
    _log(f"PLN/CZK rate: {fx:.3f}")
    try:
        with _make_fetcher(args) as fetcher:
            discrepancies, n_cz, n_pl = compare_markets(
                fetcher,
                args.query_cz,
                args.query_pl,
                fx_pln_czk=fx,
                pages=args.pages,
                max_offers=args.max_offers,
                cheap_first=args.cheap_first,
                price_from=args.price_from,
                log=_log,
            )
    except (BotBlockedError, ApifyError, BrightDataError) as exc:
        print(f"blocked/failed: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"network/browser error: {exc}", file=sys.stderr)
        return 3
    swaps = sum(1 for d in discrepancies if d.kind == "currency_swap")
    print(
        f"\ncompared {n_cz} CZK offers against {n_pl} PLN offers: "
        f"{len(discrepancies)} discrepancies ({swaps} likely currency swaps)"
    )
    return 0


def _cmd_reset(args: argparse.Namespace) -> int:
    browser = AllegroBrowser(profile_dir=args.profile)
    browser.reset_profile()
    print(f"removed browser profile at {browser.profile_dir}")
    print("wait ~15 minutes before scanning again, then start with --headful")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="allegro-deals",
        description="Find offers on allegro.cz priced in the wrong currency.",
    )
    parser.add_argument("--fx", type=float, help="override PLN/CZK rate")
    parser.add_argument(
        "--engine",
        choices=["auto", "brightdata", "apify", "browser"],
        default="auto",
        help="fetch engine (auto: brightdata > apify > browser, by available tokens)",
    )
    parser.add_argument("--apify-token", help="Apify API token (or set APIFY_TOKEN)")
    parser.add_argument("--brightdata-token", help="Bright Data API token (or set BRIGHTDATA_API_TOKEN)")
    parser.add_argument("--brightdata-zone", help="Bright Data Web Unlocker zone (or set BRIGHTDATA_ZONE)")
    parser.add_argument("--headful", action="store_true", help="browser engine: show the window (needed to solve a captcha once)")
    parser.add_argument("--profile", help="browser engine: profile dir (keeps DataDome cookies)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="search allegro.cz and flag suspicious offers")
    p_scan.add_argument("queries", nargs="+", help="search phrases, e.g. 'brio vlak'")
    p_scan.add_argument("--pages", type=int, default=1, help="search pages per query")
    p_scan.add_argument("--max-products", type=int, default=15, help="product pages to inspect per query")
    p_scan.add_argument("--max-ratio", type=float, default=DEFAULT_MAX_RATIO, help="flag offers below this fraction of the typical price")
    p_scan.add_argument("--out", default="findings.jsonl", help="append findings to this JSONL file")
    p_scan.set_defaults(func=_cmd_scan)

    p_check = sub.add_parser("check", help="cross-check one offer between allegro.cz and allegro.pl")
    p_check.add_argument("offer", help="offer id or allegro.cz URL")
    p_check.set_defaults(func=_cmd_check)

    p_cmp = sub.add_parser(
        "compare", help="search both allegro.cz and allegro.pl and flag price gaps"
    )
    p_cmp.add_argument("query_cz", help="search phrase for allegro.cz, e.g. 'brio vlak'")
    p_cmp.add_argument(
        "query_pl", nargs="?", default=None,
        help="search phrase for allegro.pl (default: same as query_cz)",
    )
    p_cmp.add_argument("--pages", type=int, default=2, help="search pages per marketplace")
    p_cmp.add_argument("--max-offers", type=int, default=100, help="offers to compare per side")
    p_cmp.add_argument("--cheap-first", action="store_true", help="sort the cz side by price ascending (hunts mispriced offers)")
    p_cmp.add_argument("--price-from", type=float, help="cz-side minimum price filter, skips cheap accessories")
    p_cmp.set_defaults(func=_cmd_compare)

    p_reset = sub.add_parser("reset", help="delete the browser profile after a DataDome block")
    p_reset.set_defaults(func=_cmd_reset)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
