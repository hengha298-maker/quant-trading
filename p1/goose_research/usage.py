"""Conservative consumer policy; observation is not investment suitability.

Shared by the local report and a future V2 adapter. Old schema-v1 records do
not gain permissions just because they predate the explicit provenance flags.
"""
from .validation import validate_market


def market_usage(document):
    validate_market(document)
    observed = document['status'] == 'observed'
    return dict(raw_observation=observed,
                historical_cost_reference=observed and document['interval'] == 'daily'
                and document['adjustment'] == '不复权' and 'provenance' not in document,
                position_valuation=False, volume_analysis=False, investment_advice=False)
