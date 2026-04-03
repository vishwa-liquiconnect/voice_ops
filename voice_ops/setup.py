"""
Voice Ops Setup

Creates default Checklist Templates from the Driver Voice System SOP
on app install. Safe to run multiple times — skips if templates already exist.
"""

import frappe


def after_install():
	create_default_templates()


def create_default_templates():
	_create_pre_boarding_template()
	_create_post_trip_template()
	frappe.db.commit()


def _create_pre_boarding_template():
	if frappe.db.exists("Checklist Template", {"template_name": "Pre-Boarding Checklist"}):
		return

	doc = frappe.new_doc("Checklist Template")
	doc.template_name = "Pre-Boarding Checklist"
	doc.checklist_type = "Pre-Departure"
	doc.is_active = 1
	doc.language = "hi-IN"
	doc.description = "Standard pre-boarding checklist as per Driver Voice System SOP. Covers vehicle confirmation, crew, documents, mechanical, electrical, cleaning, linen, supplies, safety, and tools."

	questions = [
		# Vehicle & Route Confirmation
		{"question_key": "vehicle_number", "question_text": "What is the vehicle number?", "question_text_hi": "Gaadi ka number kya hai?", "expected_response_type": "Free Text", "is_blocker": 0, "is_warning": 0, "is_required": 1},
		{"question_key": "route", "question_text": "Confirm the route", "question_text_hi": "Route confirm karein", "expected_response_type": "Free Text", "is_blocker": 0, "is_warning": 0, "is_required": 1},
		{"question_key": "service_id", "question_text": "Confirm service ID", "question_text_hi": "Service ID confirm karein", "expected_response_type": "Free Text", "is_blocker": 0, "is_warning": 0, "is_required": 1},

		# Crew & Identity
		{"question_key": "driver_1", "question_text": "Is Driver 1 available?", "question_text_hi": "Kya Driver 1 available hai?", "expected_response_type": "Yes/No", "is_blocker": 1, "is_warning": 0, "is_required": 1},
		{"question_key": "driver_2", "question_text": "Is Driver 2 available?", "question_text_hi": "Kya Driver 2 available hai?", "expected_response_type": "Yes/No", "is_blocker": 1, "is_warning": 0, "is_required": 1},
		{"question_key": "cleaner", "question_text": "Is Cleaner available?", "question_text_hi": "Kya Cleaner available hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "crew_at_end_point", "question_text": "Will both drivers be available till destination?", "question_text_hi": "Kya dono driver destination tak available rahenge?", "expected_response_type": "Yes/No", "is_blocker": 1, "is_warning": 0, "is_required": 1},
		{"question_key": "driver_fitness", "question_text": "Is the driver fit and well-rested?", "question_text_hi": "Kya driver fit hai aur rest kiya hai?", "expected_response_type": "Yes/No", "is_blocker": 1, "is_warning": 0, "is_required": 1},
		{"question_key": "vehicle_location", "question_text": "Confirm vehicle location / starting point", "question_text_hi": "Gaadi ki location / starting point confirm karein", "expected_response_type": "Free Text", "is_blocker": 0, "is_warning": 0, "is_required": 1},

		# Documents
		{"question_key": "documents_available", "question_text": "Are all documents available? (RC, Insurance, FC, Permit, PUC)", "question_text_hi": "Kya saare documents hain? (RC, Insurance, FC, Permit, PUC)", "expected_response_type": "Yes/No", "is_blocker": 1, "is_warning": 0, "is_required": 1},
		{"question_key": "documents_shared", "question_text": "If any document is missing, has it been shared via WhatsApp?", "question_text_hi": "Agar koi document missing hai toh WhatsApp pe share kiya hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 0},

		# Mechanical & Electrical
		{"question_key": "mechanical_issue", "question_text": "Is there any mechanical issue?", "question_text_hi": "Koi mechanical issue hai?", "expected_response_type": "Yes/No", "is_blocker": 1, "is_warning": 0, "is_required": 1},
		{"question_key": "electrical_issue", "question_text": "Is there any electrical issue?", "question_text_hi": "Koi electrical issue hai?", "expected_response_type": "Yes/No", "is_blocker": 1, "is_warning": 0, "is_required": 1},
		{"question_key": "ac_working", "question_text": "Is the AC working properly?", "question_text_hi": "Kya AC sahi chal raha hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "ac_condition", "question_text": "If AC issue, which seat/section?", "question_text_hi": "Agar AC mein issue hai toh kaunsi seat/section?", "expected_response_type": "Free Text", "is_blocker": 0, "is_warning": 1, "is_required": 0},
		{"question_key": "charging_points", "question_text": "Are charging points working?", "question_text_hi": "Kya charging points chal rahe hain?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "inverter", "question_text": "Is the inverter working?", "question_text_hi": "Kya inverter chal raha hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "lights", "question_text": "Are all lights working?", "question_text_hi": "Kya saari lights chal rahi hain?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "speaker", "question_text": "Is the speaker working?", "question_text_hi": "Kya speaker chal raha hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "amplifier", "question_text": "Is the amplifier working?", "question_text_hi": "Kya amplifier chal raha hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "mic", "question_text": "Is the MIC working?", "question_text_hi": "Kya MIC chal raha hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},

		# Cleaning
		{"question_key": "interior_cleaning", "question_text": "Is interior cleaning done?", "question_text_hi": "Kya andar ki safai ho gayi hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "seat_cleaning", "question_text": "Is seat cleaning done?", "question_text_hi": "Kya seat ki safai ho gayi hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "washroom_clean", "question_text": "Is the washroom clean?", "question_text_hi": "Kya washroom saaf hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "room_freshener", "question_text": "Has room freshener been applied?", "question_text_hi": "Kya room freshener lagaya gaya hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "bad_smell", "question_text": "Is there any bad smell?", "question_text_hi": "Koi buri smell aa rahi hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "cleaning_kit", "question_text": "Is the cleaning kit available?", "question_text_hi": "Kya cleaning kit available hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},

		# Linen
		{"question_key": "curtains_clean", "question_text": "Are curtains clean?", "question_text_hi": "Kya parde saaf hain?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "blankets_clean", "question_text": "Are blankets clean?", "question_text_hi": "Kya kambal saaf hain?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "bedsheets_clean", "question_text": "Are bedsheets clean?", "question_text_hi": "Kya bedsheet saaf hain?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "last_wash_date", "question_text": "When was the last linen wash?", "question_text_hi": "Linen ki last dhulai kab hui thi?", "expected_response_type": "Free Text", "is_blocker": 0, "is_warning": 0, "is_required": 1},
		{"question_key": "linen_due", "question_text": "Is linen wash overdue?", "question_text_hi": "Kya linen ki dhulai overdue hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "blanket_count", "question_text": "How many blankets are available?", "question_text_hi": "Kitne kambal available hain?", "expected_response_type": "Numeric", "is_blocker": 0, "is_warning": 0, "is_required": 1},
		{"question_key": "extra_blankets", "question_text": "Are there at least 5 extra blankets?", "question_text_hi": "Kya kam se kam 5 extra kambal hain?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 0, "is_required": 1},

		# Supplies & Water
		{"question_key": "water_bottles", "question_text": "Is water bottle stock sufficient?", "question_text_hi": "Kya paani ki bottles ka stock kaafi hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "extra_water", "question_text": "Is extra water available (seat + sleeper + 5)?", "question_text_hi": "Kya extra paani hai (seat + sleeper + 5)?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "washroom_water", "question_text": "Is washroom water tank filled?", "question_text_hi": "Kya washroom ka paani ka tank bhara hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "washroom_exhaust", "question_text": "Is the washroom exhaust working?", "question_text_hi": "Kya washroom ka exhaust chal raha hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},

		# Safety
		{"question_key": "door_locks", "question_text": "Are all door locks working?", "question_text_hi": "Kya saare door locks chal rahe hain?", "expected_response_type": "Yes/No", "is_blocker": 1, "is_warning": 0, "is_required": 1},
		{"question_key": "emergency_exit", "question_text": "Is the emergency exit accessible?", "question_text_hi": "Kya emergency exit accessible hai?", "expected_response_type": "Yes/No", "is_blocker": 1, "is_warning": 0, "is_required": 1},
		{"question_key": "fire_extinguisher", "question_text": "Is the fire extinguisher available and charged?", "question_text_hi": "Kya fire extinguisher available hai aur charged hai?", "expected_response_type": "Yes/No", "is_blocker": 1, "is_warning": 0, "is_required": 1},

		# Tools
		{"question_key": "hammer", "question_text": "Is the hammer available?", "question_text_hi": "Kya hammer available hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "toolkit", "question_text": "Is the toolkit available?", "question_text_hi": "Kya toolkit available hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "stepney", "question_text": "Is the stepney available?", "question_text_hi": "Kya stepney available hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "jack", "question_text": "Is the jack available?", "question_text_hi": "Kya jack available hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "lever", "question_text": "Is the lever available?", "question_text_hi": "Kya lever available hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},

		# Vehicle Condition
		{"question_key": "tyres", "question_text": "Are all tyres in good condition?", "question_text_hi": "Kya saare tyre sahi condition mein hain?", "expected_response_type": "Yes/No", "is_blocker": 1, "is_warning": 0, "is_required": 1},
		{"question_key": "exterior_damage", "question_text": "Is there any exterior damage?", "question_text_hi": "Koi bahari damage hai?", "expected_response_type": "Free Text", "is_blocker": 0, "is_warning": 0, "is_required": 1},
		{"question_key": "branding", "question_text": "Is vehicle branding OK?", "question_text_hi": "Kya gaadi ki branding theek hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},

		# Final Status
		{"question_key": "ready_status", "question_text": "Is the vehicle ready for departure?", "question_text_hi": "Kya gaadi departure ke liye ready hai?", "expected_response_type": "Yes/No", "is_blocker": 1, "is_warning": 0, "is_required": 1},
	]

	for i, q in enumerate(questions):
		q["sequence"] = i + 1
		doc.append("questions", q)

	doc.insert(ignore_permissions=True)
	frappe.msgprint(f"Created Pre-Boarding Checklist with {len(questions)} questions")


def _create_post_trip_template():
	if frappe.db.exists("Checklist Template", {"template_name": "Post-Trip Checklist"}):
		return

	doc = frappe.new_doc("Checklist Template")
	doc.template_name = "Post-Trip Checklist"
	doc.checklist_type = "Post-Arrival"
	doc.is_active = 1
	doc.language = "hi-IN"
	doc.description = "Standard post-trip checklist as per Driver Voice System SOP. Covers arrival, crew, complaints, AC, interior, linen, service, diesel, incidents, and next trip readiness."

	questions = [
		# Arrival
		{"question_key": "destination_time", "question_text": "What time did you reach the destination?", "question_text_hi": "Aap destination pe kab pahunche?", "expected_response_type": "Free Text", "is_blocker": 0, "is_warning": 0, "is_required": 1},
		{"question_key": "delay", "question_text": "Was there any delay?", "question_text_hi": "Kya koi delay hua?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "delay_reason", "question_text": "If delayed, what was the reason?", "question_text_hi": "Agar delay hua toh kya reason tha?", "expected_response_type": "Free Text", "is_blocker": 0, "is_warning": 0, "is_required": 0},

		# Crew at Destination
		{"question_key": "driver_1_end", "question_text": "Is Driver 1 available at destination?", "question_text_hi": "Kya Driver 1 destination pe available hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "driver_2_end", "question_text": "Is Driver 2 available at destination?", "question_text_hi": "Kya Driver 2 destination pe available hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "cleaner_end", "question_text": "Is Cleaner available at destination?", "question_text_hi": "Kya Cleaner destination pe available hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "crew_issue", "question_text": "Any crew-related issue?", "question_text_hi": "Koi crew se related issue hai?", "expected_response_type": "Free Text", "is_blocker": 0, "is_warning": 1, "is_required": 1},

		# Mechanical & Electrical Complaints
		{"question_key": "mechanical_complaint", "question_text": "Any mechanical complaint during the trip?", "question_text_hi": "Trip ke dauraan koi mechanical complaint hai?", "expected_response_type": "Free Text", "is_blocker": 1, "is_warning": 0, "is_required": 1},
		{"question_key": "electrical_complaint", "question_text": "Any electrical complaint during the trip?", "question_text_hi": "Trip ke dauraan koi electrical complaint hai?", "expected_response_type": "Free Text", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "tyre_issue", "question_text": "Any tyre issue during the trip?", "question_text_hi": "Trip ke dauraan koi tyre issue hua?", "expected_response_type": "Free Text", "is_blocker": 1, "is_warning": 0, "is_required": 1},
		{"question_key": "exterior_damage", "question_text": "Any exterior damage noticed?", "question_text_hi": "Koi bahari damage dikha?", "expected_response_type": "Free Text", "is_blocker": 0, "is_warning": 0, "is_required": 1},

		# AC & Electronics
		{"question_key": "ac_performance", "question_text": "How was the AC performance?", "question_text_hi": "AC ka performance kaisa tha?", "expected_response_type": "Select", "select_options": "OK\nIssue", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "ac_details", "question_text": "If AC issue, provide details", "question_text_hi": "Agar AC mein issue hai toh details dein", "expected_response_type": "Free Text", "is_blocker": 0, "is_warning": 0, "is_required": 0},

		# Interior & Washroom
		{"question_key": "interior_feedback", "question_text": "Any interior feedback?", "question_text_hi": "Andar ki koi feedback hai?", "expected_response_type": "Free Text", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "washroom_feedback", "question_text": "Any washroom feedback?", "question_text_hi": "Washroom ki koi feedback hai?", "expected_response_type": "Free Text", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "bad_smell", "question_text": "Was there any bad smell?", "question_text_hi": "Koi buri smell thi?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},

		# Linen & Service
		{"question_key": "linen_due", "question_text": "Is linen wash due?", "question_text_hi": "Kya linen ki dhulai due hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "linen_status", "question_text": "Linen status - washed or not?", "question_text_hi": "Linen ka status - dhula ya nahi?", "expected_response_type": "Select", "select_options": "Washed\nNot Washed", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "service_due", "question_text": "Is vehicle service due?", "question_text_hi": "Kya gaadi ki service due hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "service_commitment", "question_text": "Any service commitment details?", "question_text_hi": "Koi service commitment details hain?", "expected_response_type": "Free Text", "is_blocker": 0, "is_warning": 0, "is_required": 0},

		# Electronics Issues
		{"question_key": "charging_issue", "question_text": "Any charging point issue?", "question_text_hi": "Koi charging point issue hai?", "expected_response_type": "Free Text", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "inverter_issue", "question_text": "Any inverter issue?", "question_text_hi": "Koi inverter issue hai?", "expected_response_type": "Free Text", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "speaker_issue", "question_text": "Any speaker issue?", "question_text_hi": "Koi speaker issue hai?", "expected_response_type": "Free Text", "is_blocker": 0, "is_warning": 1, "is_required": 1},

		# Diesel & Idling
		{"question_key": "diesel_exception", "question_text": "Any diesel exception?", "question_text_hi": "Koi diesel exception hai?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 0, "is_required": 1},
		{"question_key": "diesel_reason", "question_text": "If diesel exception, what was the reason?", "question_text_hi": "Agar diesel exception hai toh kya reason hai?", "expected_response_type": "Free Text", "is_blocker": 0, "is_warning": 0, "is_required": 0},
		{"question_key": "idling_high", "question_text": "Was idling unusually high?", "question_text_hi": "Kya idling zyada thi?", "expected_response_type": "Yes/No", "is_blocker": 0, "is_warning": 0, "is_required": 1},
		{"question_key": "idling_reason", "question_text": "If high idling, what was the reason?", "question_text_hi": "Agar idling zyada thi toh kya reason hai?", "expected_response_type": "Free Text", "is_blocker": 0, "is_warning": 0, "is_required": 0},
		{"question_key": "driving_behaviour", "question_text": "Any driving behaviour concerns? (OBD + input)", "question_text_hi": "Koi driving behaviour concern hai?", "expected_response_type": "Free Text", "is_blocker": 0, "is_warning": 0, "is_required": 1},

		# Passenger & Incidents
		{"question_key": "passenger_complaint", "question_text": "Any passenger complaint?", "question_text_hi": "Koi passenger complaint hai?", "expected_response_type": "Free Text", "is_blocker": 0, "is_warning": 1, "is_required": 1},
		{"question_key": "incident", "question_text": "Was there any incident or breakdown?", "question_text_hi": "Koi incident ya breakdown hua?", "expected_response_type": "Free Text", "is_blocker": 1, "is_warning": 0, "is_required": 1},

		# Readiness
		{"question_key": "next_trip_ready", "question_text": "Is the vehicle ready for the next trip?", "question_text_hi": "Kya gaadi next trip ke liye ready hai?", "expected_response_type": "Yes/No", "is_blocker": 1, "is_warning": 0, "is_required": 1},

		# Comments
		{"question_key": "comments", "question_text": "Any other comments?", "question_text_hi": "Koi aur comment hai?", "expected_response_type": "Free Text", "is_blocker": 0, "is_warning": 0, "is_required": 0},
	]

	for i, q in enumerate(questions):
		q["sequence"] = i + 1
		doc.append("questions", q)

	doc.insert(ignore_permissions=True)
	frappe.msgprint(f"Created Post-Trip Checklist with {len(questions)} questions")
