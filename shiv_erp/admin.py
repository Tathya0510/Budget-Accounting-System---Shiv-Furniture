from __future__ import annotations

from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from .models import (
    AnalyticalAccount,
    AutoAnalyticalRule,
    Budget,
    BudgetPeriod,
    BudgetRevision,
    Contact,
    Document,
    DocumentLine,
    Payment,
    Product,
)


class DocumentInline(admin.TabularInline):
    model = Document
    extra = 0
    fields = ("number", "doc_type", "issue_date", "status", "total_amount", "payment_status")
    readonly_fields = ("number", "doc_type", "issue_date", "status", "total_amount", "payment_status")
    can_delete = False
    show_change_link = True
    classes = ["collapse"]
    verbose_name = _("Related Document")
    verbose_name_plural = _("Related Documents")


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("name", "contact_type", "email", "phone", "is_active")
    list_filter = ("contact_type", "is_active")
    search_fields = ("name", "email", "phone")
    inlines = [DocumentInline]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "sku", "category", "default_unit_price", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("name", "sku", "category")


@admin.register(AnalyticalAccount)
class AnalyticalAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "parent", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code")


@admin.register(BudgetPeriod)
class BudgetPeriodAdmin(admin.ModelAdmin):
    list_display = ("name", "start_date", "end_date", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


class BudgetRevisionInline(admin.TabularInline):
    model = BudgetRevision
    extra = 0
    fields = ("revised_amount", "revised_by", "note", "created_at")
    readonly_fields = ("created_at",)
    can_delete = False


@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = ("analytic_account", "period", "kind", "amount", "actual_amount", "variance", "achievement_percent")
    list_filter = ("kind", "period", "is_active")
    search_fields = ("analytic_account__name", "analytic_account__code", "period__name")
    inlines = [BudgetRevisionInline]

    def save_model(self, request: HttpRequest, obj: Budget, form, change: bool) -> None:
        previous_amount = None
        if change and obj.pk:
            previous_amount = Budget.objects.filter(pk=obj.pk).values_list("amount", flat=True).first()
        super().save_model(request, obj, form, change)
        if previous_amount is not None and previous_amount != obj.amount:
            BudgetRevision.objects.create(budget=obj, revised_amount=obj.amount, revised_by=request.user)


@admin.register(AutoAnalyticalRule)
class AutoAnalyticalRuleAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "transaction_type",
        "priority",
        "match_contact",
        "match_product",
        "match_product_category",
        "assign_analytic_account",
        "is_active",
    )
    list_filter = ("transaction_type", "is_active")
    search_fields = ("name", "match_product_category", "assign_analytic_account__name")


class DocumentLineInline(admin.TabularInline):
    model = DocumentLine
    extra = 1
    fields = ("product", "description", "quantity", "unit_price", "line_total", "analytic_account")
    readonly_fields = ("line_total",)


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    fields = ("payment_date", "method", "amount", "status")


@admin.action(description=_("Confirm selected documents"))
def confirm_documents(modeladmin: admin.ModelAdmin, request: HttpRequest, queryset):
    for doc in queryset:
        try:
            doc.confirm()
        except ValidationError as e:
            modeladmin.message_user(request, f"{doc}: {e}", level=messages.ERROR)


@admin.action(description=_("Post selected invoices/bills"))
def post_documents(modeladmin: admin.ModelAdmin, request: HttpRequest, queryset):
    for doc in queryset:
        try:
            doc.post()
        except ValidationError as e:
            modeladmin.message_user(request, f"{doc}: {e}", level=messages.ERROR)


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("number", "doc_type", "contact", "issue_date", "status", "total_amount", "payment_status")
    list_filter = ("doc_type", "status", "payment_status")
    search_fields = ("number", "contact__name")
    date_hierarchy = "issue_date"
    inlines = [DocumentLineInline, PaymentInline]
    actions = [confirm_documents, post_documents]

    def save_related(self, request: HttpRequest, form, formsets, change: bool) -> None:
        with transaction.atomic():
            super().save_related(request, form, formsets, change)
            doc: Document = form.instance
            doc.recalculate_totals()
            doc.save()


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("document", "payment_date", "method", "amount", "status")
    list_filter = ("method", "status")
    search_fields = ("document__number", "document__contact__name")
