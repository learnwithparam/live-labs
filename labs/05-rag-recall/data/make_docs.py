"""Write Ledgerline's synthetic runbooks and policies as PDFs (`docs/*.pdf`), each page with the header
and footer a real wiki export prints. Seeded by its text, so a rerun writes the same pages.

    uv run --with reportlab==4.4.4 python data/make_docs.py
"""

from html import escape
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

HERE = Path(__file__).parent
DOCS = {
    "ledger-writer-runbook": ("RB-101 Ledger writer runbook", [
        ("Service", "ledger-writer takes settled payment events from Kafka and writes them to the ledger-pg cluster. It runs in eu-west-1 and us-east-1, six pods per region, in the payments namespace."),
        ("Alert: LedgerWriterErrorRate", "Fires when more than 2% of writes fail for 5 minutes. Open the Ledger Writer dashboard and check the error panel by region. Run ledgerctl status --service ledger-writer to see which pods are failing and why."),
        ("Mitigation", "If one region fails and the other is healthy, take the failing region out of traffic with ledgerctl drain --region eu-west-1 (or us-east-1). Draining holds new events in Kafka; nothing is lost. Undrain with ledgerctl undrain once the region is healthy."),
        ("Rollback", "If the errors started within an hour of a deploy, roll back first and investigate after: kubectl rollout undo deployment/ledger-writer -n payments. Watch the error panel for ten minutes before you call it fixed."),
        ("Escalation", "If the error rate is still above 2% after 20 minutes, page the database team in #payments-db with the dashboard link and the output of ledgerctl status."),
    ]),
    "kafka-consumer-lag": ("RB-102 Settlement consumer lag", [
        ("Alert: SettlementConsumerLag", "Fires when the settlement-consumer group is more than 250,000 messages behind for 10 minutes. Lag means payouts are late, not lost."),
        ("Find the cause", "Open the consumer lag dashboard. Lag on every partition usually means the consumers are slow or crashing; lag on one partition usually means a poison message the consumer cannot parse."),
        ("Poison message", "Find the offset in the consumer logs, copy the message to the dead letter topic, then skip it with kcat-skip --group settlement-consumer --partition <n> --offset <offset>. Record the offset in the incident channel so finance can settle it by hand."),
        ("Scaling", "The topic has 24 partitions, so more than 24 consumers do nothing. Scale with kubectl scale deployment/settlement-consumer --replicas=24 -n payments."),
        ("Never", "Never reset the group offsets to latest to clear lag. Every skipped message is a payout that is never settled."),
    ]),
    "postgres-failover": ("RB-103 ledger-pg failover", [
        ("When to fail over", "Fail over only when the primary is down or will not accept writes for more than 3 minutes. A slow primary is not a reason to fail over."),
        ("Check replica lag", "Before promoting, check the candidate's lag on the replica: SELECT now() - pg_last_xact_replay_timestamp(); Promote only when the lag is below 5 seconds. A replica further behind loses committed ledger entries."),
        ("Fail over", "Run patronictl failover ledger-pg --candidate ledger-pg-2 from the bastion. Patroni moves the leader and updates the service endpoint."),
        ("After the failover", "Reload the connection pool with pgbouncer RELOAD on both pool hosts, then confirm ledger-writer errors drop. Open a ticket to rebuild the old primary as a replica."),
    ]),
    "payout-writer-runbook": ("RB-104 Payout writer runbook", [
        ("Service", "payout-writer sends approved payouts to the banking partner and records each transfer. It runs in eu-west-1 and us-east-1, four pods per region, in the payouts namespace."),
        ("Alert: PayoutWriterErrorRate", "Fires when more than 1% of transfers fail for 5 minutes. Open the Payout Writer dashboard and check the partner error panel. Run payoutctl status --service payout-writer to see which pods are failing."),
        ("Mitigation", "If one region fails and the other is healthy, take the failing region out of traffic with payoutctl pause --region eu-west-1 (or us-east-1). Pausing holds approved payouts in the queue; none is sent twice."),
        ("Rollback", "If the errors started after a deploy, roll back first: kubectl rollout undo deployment/payout-writer -n payouts. Partner errors that started without a deploy are not fixed by a rollback."),
        ("Partner outage", "When the banking partner's status page shows an incident, do not retry by hand. Payouts wait in the queue and payout-writer retries every 10 minutes for 24 hours. Tell support the expected delay."),
    ]),
    "card-auth-gateway": ("RB-105 Card authorisation gateway", [
        ("Service", "card-auth forwards card authorisations from merchants to the acquirer and returns approve or decline. Merchants wait on it at checkout, so its latency is the customer's latency."),
        ("Alert: CardAuthLatencyHigh", "Fires when p99 authorisation latency is above 1.5 seconds for 5 minutes. Check the acquirer panel first: most slowness comes from the acquirer, not from card-auth."),
        ("Error codes", "AUTH-503 means the acquirer did not answer in time and the payment was declined as a timeout. AUTH-429 means a merchant passed its rate limit. AUTH-402 is a decline from the issuing bank and needs no action from us."),
        ("Acquirer failover", "If the primary acquirer times out on more than 5% of authorisations, switch to the backup acquirer with authctl route --acquirer backup. The backup costs 0.1 percentage points more per payment, so switch back within a day."),
        ("Circuit breaker", "card-auth opens its circuit breaker after 50 timeouts in a minute and declines fast instead of waiting. A declined payment can be retried by the shopper; a hung checkout is abandoned."),
    ]),
    "fx-rates-service": ("RB-106 FX rates service", [
        ("Service", "fx-rates pulls exchange rates from two providers every minute and serves them to pricing and payouts. Rates older than 10 minutes are stale."),
        ("Alert: FxRatesStale", "Fires when the newest rate is more than 10 minutes old. Check both provider panels: if one provider stopped, fx-rates already uses the other."),
        ("Freeze rates", "If both providers fail, freeze the last good rates with fxctl freeze --max-age 2h. Pricing keeps working on frozen rates for two hours; after that, cross-currency checkout is turned off."),
        ("Manual rate", "Never type a rate in by hand. A wrong manual rate has to be refunded to every customer it touched."),
    ]),
    "webhook-dispatcher": ("RB-107 Merchant webhook dispatcher", [
        ("Service", "hook-dispatcher sends payment events to merchants' webhook endpoints and signs each one with the merchant's secret."),
        ("Retries", "A failed delivery is retried with backoff: after 1 minute, 10 minutes, 1 hour, then every 6 hours. After 72 hours of failures the endpoint is disabled and the merchant is emailed."),
        ("Replay events", "To resend what a merchant missed, run hookctl replay --merchant <id> --since <time>. Replays are signed again and carry the original event id, so the merchant can drop duplicates."),
        ("Disabled endpoint", "A merchant whose endpoint was disabled must fix it and press Re-enable in the dashboard. Support cannot re-enable it for them; that stops us sending events to a URL nobody owns."),
        ("Signing secret", "Rotate a merchant's signing secret only when they ask or it has leaked. The old secret keeps working for 24 hours so the merchant can deploy the new one."),
    ]),
    "reconciliation-job": ("RB-108 Nightly reconciliation", [
        ("Job", "recon runs at 02:00 UTC and matches every ledger entry from the day before to the bank's settlement file. It writes a mismatch report to the finance bucket."),
        ("RECON-MISMATCH-17", "The bank's file has a payment the ledger does not. This is almost always a late settlement event still in Kafka. Wait for the consumer to catch up, then rerun."),
        ("RECON-MISMATCH-22", "The ledger has a payment the bank's file does not. Do not delete the ledger entry. Send the payment id to finance in #finance-ops; they raise it with the bank."),
        ("Rerun", "Rerun a day with reconctl rerun --date YYYY-MM-DD. A rerun replaces that day's report; it never changes the ledger."),
        ("Bank file late", "If the bank's settlement file has not arrived by 04:00 UTC, the job waits and retries every 30 minutes until 10:00 UTC, then alerts finance."),
    ]),
    "idempotency-cache": ("RB-109 Idempotency cache", [
        ("Service", "idem-cache is the Redis cluster that remembers each payment request's idempotency key for 24 hours, so a retried request is never charged twice."),
        ("Alert: IdemCacheMemoryHigh", "Fires above 85% memory. Keys expire on their own after 24 hours, so a spike usually follows a traffic spike. Add a shard with redisctl add-shard idem-cache before memory reaches 95%."),
        ("Never flush", "Never run FLUSHALL on idem-cache. Every retried payment in the next 24 hours could be charged twice."),
        ("Failover", "If the primary node dies, the Redis sentinel promotes a replica within 30 seconds. Payments in that window are declined and retried by merchants; none is charged twice."),
    ]),
    "incident-severity": ("POL-02 Incident severity", [
        ("Severity levels", "SEV1: money movement has stopped, or a customer sees a wrong balance. SEV2: payments are slow or one region is degraded. SEV3: an internal tool is broken."),
        ("Paging", "For a SEV1 or SEV2, page the payments incident commander with /page ic-payments in Slack. Do not wait to be sure; a downgraded page costs less than a late one."),
        ("Status page", "For a SEV1, post the first status page update within 15 minutes of declaring it, and every 30 minutes after."),
        ("Postmortem", "Every SEV1 and SEV2 gets a written postmortem within 5 business days, reviewed at the Thursday reliability meeting."),
    ]),
    "deploy-policy": ("POL-07 Deploy policy", [
        ("Freeze window", "No deploys to payments services from Friday 16:00 UTC to Monday 08:00 UTC, and none during a declared incident unless the deploy is the fix."),
        ("Emergency deploys", "An emergency deploy in the freeze needs approval from the incident commander and a second engineer, both posted in #deploy-approvals before the deploy starts."),
        ("Canary", "Every deploy goes to a 5% canary for 30 minutes. Promote only if the canary error rate is no higher than the stable pods."),
        ("Feature flags", "Turning a flag on for all merchants counts as a deploy and follows the freeze. Turning a flag off to stop harm never needs approval."),
        ("Database migrations", "A migration that rewrites a table runs on its own deploy, never with code that depends on it, and never in the freeze."),
    ]),
    "access-and-secrets": ("POL-09 Access and secrets", [
        ("Break-glass access", "Production database access is off by default. In an incident, request it with vaultctl breakglass --reason <ticket>; it lasts one hour and the security team is told at once."),
        ("Secret rotation", "Service secrets rotate every 90 days on their own. A secret that was pasted anywhere outside the vault is rotated the same day."),
        ("Tickets and chat", "Never paste a secret, a token or a card number into a ticket or Slack. Link to the vault path instead."),
        ("Leaving the company", "When someone leaves, their access is removed on their last day and every shared secret they could read is rotated within a week."),
    ]),
    "data-retention": ("POL-11 Card data and retention", [
        ("Card numbers", "Full card numbers never leave the vault service. Logs, tickets and analytics hold the last four digits and the token only."),
        ("Masking in logs", "A log line with a full card number is a security incident. Open a SEV2, purge the log index, and tell the security team."),
        ("Retention", "Ledger entries are kept for 7 years, as financial law asks. Support transcripts are kept for 2 years, then deleted."),
        ("Deletion requests", "A customer's request to delete their data is completed within 30 days. Ledger entries are kept, with the name and email removed."),
    ]),
    "on-call-handbook": ("POL-04 On-call handbook", [
        ("Response time", "Acknowledge a page within 5 minutes and be at a laptop within 15. If you cannot, escalate to the secondary at once."),
        ("Handover", "At the end of a shift, write a handover in #payments-oncall: open incidents, silenced alerts, and anything the next person must watch."),
        ("Escalation chain", "Primary on-call, then secondary on-call, then the engineering manager for payments, then the head of engineering."),
        ("Swapping shifts", "Swap a shift with anyone on the rota, then change it in PagerDuty yourself. A swap that is only agreed in Slack does not move the pages."),
        ("Weekend pay", "Each weekend day on call is paid at a flat rate, plus time off in lieu for any night you were paged after 23:00."),
    ]),
}


def main() -> None:
    styles = getSampleStyleSheet()
    for slug, (title, sections) in DOCS.items():
        def page(canvas, doc, title=title):
            canvas.setFont("Helvetica", 8)
            canvas.drawString(56, 810, "Ledgerline Payments Platform - Internal")
            canvas.drawString(56, 28, f"{title.split()[0]} rev 7 - page {doc.page}")

        story = [Paragraph(title, styles["Title"])]
        for heading, text in sections:
            story += [Paragraph(escape(heading), styles["Heading2"]), Paragraph(escape(text), styles["BodyText"]), Spacer(1, 60)]
        out = HERE / "docs" / f"{slug}.pdf"
        SimpleDocTemplate(str(out), pagesize=A4, title=title, invariant=1).build(story, onFirstPage=page, onLaterPages=page)
    print(f"{len(DOCS)} PDFs in {HERE / 'docs'}")


if __name__ == "__main__":
    main()
