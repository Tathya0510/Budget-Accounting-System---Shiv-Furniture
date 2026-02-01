from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from .models import AutoAnalyticalRule, Document, DocumentLine


@dataclass(frozen=True)
class AutoAnalyticsResult:
    updated_lines: int
    applied_rule_ids: set[int]


@transaction.atomic
def apply_auto_analytics(*, document: Document) -> AutoAnalyticsResult:
    rules = list(
        AutoAnalyticalRule.objects.filter(is_active=True, transaction_type=document.doc_type).order_by("priority", "id")
    )
    if not rules:
        return AutoAnalyticsResult(updated_lines=0, applied_rule_ids=set())

    updated_lines = 0
    applied_rule_ids: set[int] = set()

    lines = list(DocumentLine.objects.select_related("product").filter(document=document))
    for line in lines:
        if line.analytic_account_id:
            continue
        for rule in rules:
            if rule.matches(document=document, line=line):
                line.analytic_account = rule.assign_analytic_account
                line.save(update_fields=["analytic_account", "line_total", "description", "updated_at"])
                updated_lines += 1
                applied_rule_ids.add(rule.id)
                break

    for rule in rules:
        if rule.matches(document=document, line=None):
            for line in lines:
                if not line.analytic_account_id:
                    line.analytic_account = rule.assign_analytic_account
                    line.save(update_fields=["analytic_account", "line_total", "description", "updated_at"])
                    updated_lines += 1
            if updated_lines:
                applied_rule_ids.add(rule.id)
            break

    return AutoAnalyticsResult(updated_lines=updated_lines, applied_rule_ids=applied_rule_ids)

