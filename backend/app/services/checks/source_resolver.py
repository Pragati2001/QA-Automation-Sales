"""
Resolve the expected value of a factual check from its configured source of truth.

`source_of_truth` is a dot-path whose first segment names the source:

    "CRM.email"                 -> crm_fields["email"]
    "RETAILER_PLAN.rates.peak"  -> retailer_plan["rates"]["peak"]

Anything missing along the way gives None. It never raises, and it never falls
back to a different source than the one configured (a CRM check with no CRM value
is "no expected value", not "look in the plan instead").
"""

import datetime as dt
from collections.abc import Mapping
from typing import Any

SOURCES = ("CRM", "RETAILER_PLAN")


def resolve_expected_value(
    source_of_truth: Any,
    crm_fields: Mapping[str, Any] | None,
    retailer_plan: Mapping[str, Any] | None,
) -> Any | None:
    if not isinstance(source_of_truth, str):
        return None
    source, _, path = source_of_truth.strip().partition(".")
    source = source.upper()
    if source not in SOURCES or not path:
        return None

    node: Any = crm_fields if source == "CRM" else retailer_plan
    for key in path.split("."):
        if not isinstance(node, Mapping) or key not in node:
            return None
        node = node[key]

    # Only a scalar is something a transcript value can be compared with.
    if isinstance(node, bool) or not isinstance(node, (str, int, float, dt.date)):
        return None
    if isinstance(node, str) and not node.strip():
        return None
    return node
