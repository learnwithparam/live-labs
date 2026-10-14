"""Write the synthetic on-call docs as PDFs (`docs/*.pdf`), each page with the header and footer a
real wiki export prints. Seeded by its text, so a rerun writes the same pages.

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
    "payout-writer-runbook": ("RB-104 Payout writer runbook", [
        ("Service", "payout-writer sends approved payouts to the banking partner and records each transfer. It runs in eu-west-1 and us-east-1, four pods per region, in the payouts namespace."),
        ("Alert: PayoutWriterErrorRate", "Fires when more than 1% of transfers fail for 5 minutes. Open the Payout Writer dashboard and check the partner error panel. Run payoutctl status --service payout-writer to see which pods are failing."),
        ("Mitigation", "If one region fails and the other is healthy, take the failing region out of traffic with payoutctl pause --region eu-west-1 (or us-east-1). Pausing holds approved payouts in the queue; none is sent twice."),
        ("Rollback", "If the errors started after a deploy, roll back first: kubectl rollout undo deployment/payout-writer -n payouts. Partner errors that started without a deploy are not fixed by a rollback."),
        ("Escalation", "If transfers are still failing after 15 minutes, page the partner integrations team in #payouts-partner with the dashboard link."),
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
    "deploy-policy": ("POL-07 Deploy policy", [
        ("Freeze window", "No deploys to payments services from Friday 16:00 UTC to Monday 08:00 UTC, and none during a declared incident unless the deploy is the fix."),
        ("Emergency deploys", "An emergency deploy in the freeze needs approval from the incident commander and a second engineer, both posted in #deploy-approvals before the deploy starts."),
        ("Canary", "Every deploy goes to a 5% canary for 30 minutes. Promote only if the canary error rate is no higher than the stable pods."),
    ]),
    "incident-severity": ("POL-02 Incident severity", [
        ("Severity levels", "SEV1: money movement has stopped, or a customer sees a wrong balance. SEV2: payments are slow or one region is degraded. SEV3: an internal tool is broken."),
        ("Paging", "For a SEV1 or SEV2, page the payments incident commander with /page ic-payments in Slack. Do not wait to be sure; a downgraded page costs less than a late one."),
        ("Status page", "For a SEV1, post the first status page update within 15 minutes of declaring it, and every 30 minutes after."),
        ("Postmortem", "Every SEV1 and SEV2 gets a written postmortem within 5 business days, reviewed at the Thursday reliability meeting."),
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
            story += [Paragraph(heading, styles["Heading2"]), Paragraph(escape(text), styles["BodyText"]), Spacer(1, 150)]
        out = HERE / "docs" / f"{slug}.pdf"
        SimpleDocTemplate(str(out), pagesize=A4, title=title, invariant=1).build(story, onFirstPage=page, onLaterPages=page)
    print(f"{len(DOCS)} PDFs in {HERE / 'docs'}")


if __name__ == "__main__":
    main()
