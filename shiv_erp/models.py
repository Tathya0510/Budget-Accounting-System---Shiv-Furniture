from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Contact(TimestampedModel):
    class Type(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        VENDOR = "vendor", "Vendor"
        BOTH = "both", "Customer & Vendor"

    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    contact_type = models.CharField(max_length=20, choices=Type.choices, default=Type.CUSTOMER)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="contact_profile"
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Product(TimestampedModel):
    name = models.CharField(max_length=255)
    sku = models.CharField(max_length=100, blank=True)
    category = models.CharField(max_length=100, blank=True)
    default_unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class AnalyticalAccount(TimestampedModel):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, blank=True)
    parent = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="children")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name if not self.code else f"{self.code} - {self.name}"


class BudgetPeriod(TimestampedModel):
    name = models.CharField(max_length=100)
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-start_date", "name"]
        constraints = [
            models.CheckConstraint(check=Q(end_date__gte=models.F("start_date")), name="budget_period_end_gte_start")
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.start_date} to {self.end_date})"


class Budget(TimestampedModel):
    class Kind(models.TextChoices):
        EXPENSE = "expense", "Expense"
        REVENUE = "revenue", "Revenue"

    analytic_account = models.ForeignKey(AnalyticalAccount, on_delete=models.PROTECT)
    period = models.ForeignKey(BudgetPeriod, on_delete=models.PROTECT)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.EXPENSE)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-period__start_date", "analytic_account__name", "kind"]
        constraints = [
            models.UniqueConstraint(
                fields=["analytic_account", "period", "kind"], name="unique_budget_per_cost_center_period_kind"
            )
        ]

    def __str__(self) -> str:
        return f"{self.analytic_account} / {self.period} / {self.get_kind_display()}"

    @property
    def actual_amount(self) -> Decimal:
        doc_types: list[str]
        if self.kind == Budget.Kind.EXPENSE:
            doc_types = [Document.Type.VENDOR_BILL]
        else:
            doc_types = [Document.Type.CUSTOMER_INVOICE]

        agg = (
            DocumentLine.objects.filter(
                analytic_account=self.analytic_account,
                document__doc_type__in=doc_types,
                document__status=Document.Status.POSTED,
                document__issue_date__gte=self.period.start_date,
                document__issue_date__lte=self.period.end_date,
            )
            .aggregate(total=Sum("line_total"))
            .get("total")
        )
        return (agg or Decimal("0.00")).quantize(Decimal("0.01"))

    @property
    def variance(self) -> Decimal:
        return (self.amount - self.actual_amount).quantize(Decimal("0.01"))

    @property
    def achievement_percent(self) -> Decimal:
        if self.amount == 0:
            return Decimal("0.00")
        if self.kind == Budget.Kind.EXPENSE:
            used = self.actual_amount
            return (used / self.amount * Decimal("100")).quantize(Decimal("0.01"))
        achieved = self.actual_amount
        return (achieved / self.amount * Decimal("100")).quantize(Decimal("0.01"))

    @property
    def remaining_balance(self) -> Decimal:
        if self.kind == Budget.Kind.EXPENSE:
            return (self.amount - self.actual_amount).quantize(Decimal("0.01"))
        return (self.amount - self.actual_amount).quantize(Decimal("0.01"))


class BudgetRevision(TimestampedModel):
    budget = models.ForeignKey(Budget, on_delete=models.CASCADE, related_name="revisions")
    revised_amount = models.DecimalField(max_digits=14, decimal_places=2)
    revised_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.budget} -> {self.revised_amount}"


class AutoAnalyticalRule(TimestampedModel):
    class TransactionType(models.TextChoices):
        PURCHASE_ORDER = "po", "Purchase Order"
        SALES_ORDER = "so", "Sales Order"
        VENDOR_BILL = "vendor_bill", "Vendor Bill"
        CUSTOMER_INVOICE = "customer_invoice", "Customer Invoice"

    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    priority = models.PositiveIntegerField(default=10)

    transaction_type = models.CharField(max_length=30, choices=TransactionType.choices)
    match_contact = models.ForeignKey(Contact, on_delete=models.SET_NULL, null=True, blank=True)
    match_product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True)
    match_product_category = models.CharField(max_length=100, blank=True)
    assign_analytic_account = models.ForeignKey(AnalyticalAccount, on_delete=models.PROTECT)

    class Meta:
        ordering = ["priority", "name"]

    def __str__(self) -> str:
        return self.name

    def matches(self, *, document: "Document", line: "DocumentLine | None" = None) -> bool:
        if not self.is_active:
            return False
        if document.doc_type != self.transaction_type:
            return False
        if self.match_contact_id and document.contact_id != self.match_contact_id:
            return False
        if line is None:
            return not (self.match_product_id or self.match_product_category)
        if self.match_product_id and line.product_id != self.match_product_id:
            return False
        if self.match_product_category and (not line.product or line.product.category != self.match_product_category):
            return False
        return True


class Document(TimestampedModel):
    class Type(models.TextChoices):
        PURCHASE_ORDER = "po", "Purchase Order"
        SALES_ORDER = "so", "Sales Order"
        VENDOR_BILL = "vendor_bill", "Vendor Bill"
        CUSTOMER_INVOICE = "customer_invoice", "Customer Invoice"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Confirmed"
        POSTED = "posted", "Posted"
        CANCELLED = "cancelled", "Cancelled"

    class PaymentStatus(models.TextChoices):
        NOT_PAID = "not_paid", "Not Paid"
        PARTIALLY_PAID = "partially_paid", "Partially Paid"
        PAID = "paid", "Paid"
        NOT_APPLICABLE = "na", "Not Applicable"

    number = models.CharField(max_length=50, unique=True, blank=True)
    doc_type = models.CharField(max_length=30, choices=Type.choices)
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="documents")
    issue_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    posted_at = models.DateTimeField(null=True, blank=True)

    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    paid_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    payment_status = models.CharField(
        max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.NOT_APPLICABLE
    )

    class Meta:
        ordering = ["-issue_date", "-created_at"]

    def __str__(self) -> str:
        return self.number or "Document"

    def clean(self) -> None:
        if self.doc_type in {Document.Type.VENDOR_BILL, Document.Type.CUSTOMER_INVOICE}:
            if self.payment_status == Document.PaymentStatus.NOT_APPLICABLE:
                self.payment_status = Document.PaymentStatus.NOT_PAID
        else:
            self.payment_status = Document.PaymentStatus.NOT_APPLICABLE
            self.due_date = None

        if self.status == Document.Status.POSTED and self.doc_type in {Document.Type.PURCHASE_ORDER, Document.Type.SALES_ORDER}:
            raise ValidationError("Only bills and invoices can be posted.")

    def save(self, *args, **kwargs) -> None:
        if not self.number:
            self.number = f"{self.doc_type.upper()}-{uuid.uuid4().hex[:10].upper()}"
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def is_financial(self) -> bool:
        return self.doc_type in {Document.Type.VENDOR_BILL, Document.Type.CUSTOMER_INVOICE}

    def recalculate_totals(self) -> None:
        total = (
            self.lines.aggregate(total=Sum("line_total")).get("total") or Decimal("0.00")
        )
        self.total_amount = total.quantize(Decimal("0.01"))
        self.update_payment_status(save=False)

    def update_payment_status(self, *, save: bool = True) -> None:
        if not self.is_financial:
            self.paid_amount = Decimal("0.00")
            self.payment_status = Document.PaymentStatus.NOT_APPLICABLE
            if save:
                Document.objects.filter(pk=self.pk).update(
                    paid_amount=self.paid_amount, payment_status=self.payment_status, total_amount=self.total_amount
                )
            return
        
        paid = (
            self.payments.filter(status=Payment.Status.POSTED).aggregate(total=Sum("amount")).get("total")
            or Decimal("0.00")
        )
        self.paid_amount = paid.quantize(Decimal("0.01"))
        if self.total_amount <= 0:
            self.payment_status = Document.PaymentStatus.NOT_PAID
        elif self.paid_amount <= 0:
            self.payment_status = Document.PaymentStatus.NOT_PAID
        elif self.paid_amount + Decimal("0.0001") < self.total_amount:
            self.payment_status = Document.PaymentStatus.PARTIALLY_PAID
        else:
            self.payment_status = Document.PaymentStatus.PAID

        if save:
            Document.objects.filter(pk=self.pk).update(
                total_amount=self.total_amount, paid_amount=self.paid_amount, payment_status=self.payment_status
            )

    def validate_cost_centers(self) -> None:
        missing = self.lines.filter(analytic_account__isnull=True).exists()
        if missing:
            raise ValidationError("All lines must be linked to an analytical account (cost center).")

    def confirm(self) -> None:
        if self.status != Document.Status.DRAFT:
            raise ValidationError("Only draft documents can be confirmed.")
        from .services import apply_auto_analytics

        apply_auto_analytics(document=self)
        self.validate_cost_centers()
        self.status = Document.Status.CONFIRMED
        self.save()

    def post(self) -> None:
        if self.doc_type not in {Document.Type.VENDOR_BILL, Document.Type.CUSTOMER_INVOICE}:
            raise ValidationError("Only bills and invoices can be posted.")
        if self.status not in {Document.Status.DRAFT, Document.Status.CONFIRMED}:
            raise ValidationError("Only draft or confirmed documents can be posted.")
        from .services import apply_auto_analytics

        apply_auto_analytics(document=self)
        self.validate_cost_centers()
        self.status = Document.Status.POSTED
        self.posted_at = timezone.now()
        self.recalculate_totals()
        self.save()


class DocumentLine(TimestampedModel):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True)
    description = models.CharField(max_length=255, blank=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("1.00"))
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    line_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    analytic_account = models.ForeignKey(AnalyticalAccount, on_delete=models.PROTECT, null=True, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.document} line"

    def clean(self) -> None:
        if self.quantity < 0:
            raise ValidationError("Quantity cannot be negative.")
        if self.unit_price < 0:
            raise ValidationError("Unit price cannot be negative.")

    def save(self, *args, **kwargs) -> None:
        if self.product and (not self.description):
            self.description = self.product.name
        self.line_total = (self.quantity * self.unit_price).quantize(Decimal("0.01"))
        self.full_clean()
        super().save(*args, **kwargs)
        self.document.recalculate_totals()
        self.document.save()


class Payment(TimestampedModel):
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        BANK = "bank", "Bank"
        ONLINE = "online", "Online"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        CANCELLED = "cancelled", "Cancelled"

    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name="payments")
    payment_date = models.DateField(default=timezone.localdate)
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.BANK)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.POSTED)

    class Meta:
        ordering = ["-payment_date", "-created_at"]

    def __str__(self) -> str:
        return f"{self.document} payment {self.amount}"

    def clean(self) -> None:
        if not self.document.is_financial:
            raise ValidationError("Payments can only be recorded against invoices or bills.")
        if self.amount <= 0:
            raise ValidationError("Payment amount must be greater than zero.")

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
        super().save(*args, **kwargs)
        self.document.recalculate_totals()
        self.document.save()
