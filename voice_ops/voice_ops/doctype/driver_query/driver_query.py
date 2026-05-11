"""
Driver Query DocType controller.

Captures a single inbound driver query call: the three recordings
(name, bus, query), their transcripts, an AI-generated summary, and
the resolved caller / vehicle / route-manager fields. Created by the
inbound webhook (`api/inbound_webhook.py` for Twilio,
`api/exotel_webhook.py` for Exotel) and enriched by
`jobs/process_driver_query.py`. No server-side behaviour beyond the
framework default.
"""

from frappe.model.document import Document


class DriverQuery(Document):
	pass
