Brickhaven manages rental flats. Read the tenant's maintenance message and reply with JSON only:
{"work_orders": [ ... ]}. Add one work order for every separate problem in the message.

Fields of a work order, all required:
- trade: plumbing, electrical, heating, locksmith, appliance, general, pest or lift.
- urgency: emergency, urgent or routine.
- unit: the flat the tenant names, for example "12A", or null when no flat is named. Never guess one.
- safety_flag: true when the message mentions a smell of gas, a burning smell, sparks, bare wires or a
  carbon monoxide alarm, and false otherwise.
- access_window: the time the tenant is home: morning, afternoon, evening or anytime.

Messages can be in any language. Answer in English.
