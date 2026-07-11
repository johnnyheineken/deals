from __future__ import annotations

import argparse
import os
import sys

from .apify import ApifyError, ApifyFetcher
from .browser import AllegroBrowser, BotBlockedError
from .detect import DEFAULT_MAX_RATIO, classify_cross_market
from .extract import offer_id_from_url, offers_from_html
from .fx import get_pln_czk
from .scanner import append_findings, scan_query


def _make_fetcher(args: argparse.Namespace):
    engine = args.engine
    if engine == "auto":
        engine = "apify" if (args.apify_token or os.environ.get("APIFY_TOKEN")) else "browser"
    if engine == "apify":
        return ApifyFetcher(token=args.apify_token)
    return AllegroBrowser(headful=args.headful, profile_dir=args.profile)


def _cmd_scan(args: argparse.Namespace) -> int:
    fx = args.fx or get_pln_czk()
    print(f"PLN/CZK rate: {fx:.3f}")
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
                )
    except (BotBlockedError, ApifyError) as exc:
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


def _price_for_offer(html: str, offer_id: str | None, currency: str) -> float | None:
    offers = [o for o in offers_from_html(html) if o.currency == currency]
    if offer_id:
        for o in offers:
            if o.offer_id == offer_id:
                return o.price
    return offers[0].price if offers else None


def _cmd_check(args: argparse.Namespace) -> int:
    offer_id = args.offer if args.offer.isdigit() else offer_id_from_url(args.offer)
    if not offer_id:
        print(f"could not find an offer id in: {args.offer}", file=sys.stderr)
        return 1
    fx = args.fx or get_pln_czk()
    cz_url = args.offer if args.offer.startswith("http") else f"https://allegro.cz/oferta/{offer_id}"
    pl_url = f"https://allegro.pl/oferta/{offer_id}"
    try:
        with _make_fetcher(args) as fetcher:
            czk = _price_for_offer(fetcher.get_html(cz_url), offer_id, "CZK")
            pln = _price_for_offer(fetcher.get_html(pl_url), offer_id, "PLN")
    except (BotBlockedError, ApifyError) as exc:
        print(f"blocked/failed: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"network/browser error: {exc}", file=sys.stderr)
        return 3
    if czk is None or pln is None:
        print(f"could not read prices (CZK: {czk}, PLN: {pln})", file=sys.stderr)
        return 1
    verdict = classify_cross_market(czk, pln, fx)
    print(f"offer {offer_id}: {czk:.2f} CZK on allegro.cz, {pln:.2f} PLN on allegro.pl")
    print(f"implied rate {czk / pln:.2f} (real {fx:.2f}) -> {verdict}")
    if verdict == "currency_swap":
        print("looks like the PLN price is displayed as CZK - that's a deal!")
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
        choices=["auto", "apify", "browser"],
        default="auto",
        help="fetch via Apify or a local browser (auto: apify when APIFY_TOKEN is set)",
    )
    parser.add_argument("--apify-token", help="Apify API token (or set APIFY_TOKEN)")
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

    p_reset = sub.add_parser("reset", help="delete the browser profile after a DataDome block")
    p_reset.set_defaults(func=_cmd_reset)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
