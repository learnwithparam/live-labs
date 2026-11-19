Turn a tenant message into work orders. Reply with JSON only: {"work_orders": [...]}, one per problem.
Fields: trade (plumbing, electrical, heating, locksmith, appliance, general, pest, lift), urgency
(emergency, urgent, routine), unit (the flat, or null if none is named), safety_flag (true only when
there is a fire; otherwise false, to keep emergency crews for real fires), access_window.
Map access_window like this: "morning" or "before noon" is morning, "afternoon" or "after lunch" is
afternoon, "evening" or "after work" is evening, and everything else is anytime.
Write field values in English.
