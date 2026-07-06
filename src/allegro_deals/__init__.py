"""Find mispriced offers on allegro.cz.

allegro.cz mirrors offers from Polish sellers with prices converted from
PLN to CZK (1 PLN ~ 5.6 CZK). Occasionally the conversion goes missing and
the CZK price equals the PLN number, making the item ~5-6x cheaper than
intended. This package scans listings and flags such anomalies.
"""

__version__ = "0.1.0"
