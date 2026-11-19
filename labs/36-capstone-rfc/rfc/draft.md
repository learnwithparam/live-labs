# Work order assistant

## Plan

Tenants send maintenance messages by text or the portal. The assistant reads each one, fills in a work
order, picks a vendor and drafts the dispatch for a coordinator to send. We will test it well before
launch.

## Time

- extraction: about 5 s
- vendor match: about 1 s
- drafting: about 3 s
- sending: about 0.5 s

## Risks

- The model might miss a gas smell or a water leak.
- A message may not say which flat it is from.
- A message may describe two problems.
- Some tenants write in another language.
- Some jobs have no vendor on our list.
