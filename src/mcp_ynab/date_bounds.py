"""Explicit date bounds shared across tool modules.

YNAB's transaction-list endpoints default an omitted ``since_date`` to
"one year ago" (OpenAPI 1.85+).  Any tool that documents an unbounded
("all history") read must pass an explicit far-past ``since_date`` so
older transactions cannot silently vanish from results while the tool
reports a complete answer.
"""

from datetime import date

# Far-past bound meaning "all history". YNAB accepts arbitrary ISO dates
# for ``since_date``; every real budget post-dates 1970, and an explicit
# bound is strictly safer than the API's implicit one-year truncation.
ALL_HISTORY_SINCE_DATE = date(1970, 1, 1)
