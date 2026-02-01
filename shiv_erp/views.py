from __future__ import annotations

import calendar
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import F, Max, Q, Sum
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from .forms import PortalPaymentForm, QuickEntryForm
from .models import AnalyticalAccount, Budget, BudgetPeriod, BudgetRevision, Contact, Document, DocumentLine, Payment


def staff_required(view_func):
    return user_passes_test(lambda u: u.is_active and u.is_staff)(view_func)


def _portal_contact(request: HttpRequest) -> Contact:
    user = request.user
    if not user.is_authenticated or user.is_staff:
        raise Http404()
    contact = getattr(user, "contact_profile", None)
    if not contact or not contact.is_active:
        raise Http404()
    return contact

class CustomerLoginView(auth_views.LoginView):
    template_name = "shiv_erp/customer_login.html"
    redirect_authenticated_user = True

    def form_valid(self, form):
        user = form.get_user()
        if user.is_staff:
            form.add_error(None, "Staff accounts must use the admin login.")
            return self.form_invalid(form)
        contact = getattr(user, "contact_profile", None)
        if not contact or not contact.is_active:
            form.add_error(None, "No active customer profile is linked to this account.")
            return self.form_invalid(form)
        return super().form_valid(form)


customer_login_view = CustomerLoginView.as_view()
logout_view = auth_views.LogoutView.as_view()


def login_choice(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect(reverse("home"))

    next_url = request.GET.get("next") or ""

    customer_login_url = reverse("customer_login")
    admin_login_url = reverse("admin:login")

    customer_next = reverse("portal_dashboard")
    admin_next = reverse("budget_report")

    if next_url:
        if next_url.startswith("/portal"):
            customer_next = next_url
        else:
            admin_next = next_url

    customer_login_url = f"{customer_login_url}?{urlencode({'next': customer_next})}"
    admin_login_url = f"{admin_login_url}?{urlencode({'next': admin_next})}"

    return render(
        request,
        "shiv_erp/login_choice.html",
        {"customer_login_url": customer_login_url, "admin_login_url": admin_login_url},
    )


@login_required(login_url="customer_login")
def portal_dashboard(request: HttpRequest) -> HttpResponse:
    if request.user.is_staff:
        return redirect(reverse("budget_report"))
    contact = _portal_contact(request)
    all_docs = Document.objects.filter(contact=contact)
    recent_docs = all_docs.order_by("-issue_date", "-created_at")[:10]
    invoices = all_docs.filter(doc_type=Document.Type.CUSTOMER_INVOICE)
    bills = all_docs.filter(doc_type=Document.Type.VENDOR_BILL)
    pending_docs = all_docs.filter(status=Document.Status.POSTED, total_amount__gt=F("paid_amount"))
    invoice_list = invoices.order_by("-issue_date", "-created_at")[:10]
    invoice_rows = []
    for inv in invoice_list:
        amount_due = (inv.total_amount - inv.paid_amount).quantize(Decimal("0.01"))
        if amount_due < 0:
            amount_due = Decimal("0.00")
        invoice_rows.append({"doc": inv, "amount_due": amount_due})
    pending_rows = []
    for doc in pending_docs.order_by("due_date", "-issue_date", "-created_at")[:10]:
        amount_due = (doc.total_amount - doc.paid_amount).quantize(Decimal("0.01"))
        if amount_due < 0:
            amount_due = Decimal("0.00")
        pending_rows.append({"doc": doc, "amount_due": amount_due})
    context = {
        "contact": contact,
        "recent_docs": recent_docs,
        "invoice_count": invoices.count(),
        "bill_count": bills.count(),
        "pending_count": pending_docs.count(),
        "pending_rows": pending_rows,
        "invoice_rows": invoice_rows,
    }
    return render(request, "shiv_erp/portal_dashboard.html", context)


@login_required(login_url="customer_login")
def portal_documents(request: HttpRequest, doc_type: str | None = None) -> HttpResponse:
    contact = _portal_contact(request)
    all_docs = Document.objects.filter(contact=contact)
    qs = all_docs.order_by("-issue_date", "-created_at")
    if doc_type:
        qs = qs.filter(doc_type=doc_type)
    invoices = all_docs.filter(doc_type=Document.Type.CUSTOMER_INVOICE).order_by("-issue_date", "-created_at")
    invoice_rows = []
    for inv in invoices[:10]:
        amount_due = (inv.total_amount - inv.paid_amount).quantize(Decimal("0.01"))
        if amount_due < 0:
            amount_due = Decimal("0.00")
        invoice_rows.append({"doc": inv, "amount_due": amount_due})
    return render(
        request,
        "shiv_erp/portal_documents.html",
        {
            "contact": contact,
            "documents": qs,
            "selected_type": doc_type,
            "invoice_rows": invoice_rows,
            "invoice_count": invoices.count(),
        },
    )


@login_required(login_url="customer_login")
def portal_document_detail(request: HttpRequest, number: str) -> HttpResponse:
    contact = _portal_contact(request)
    doc = get_object_or_404(Document.objects.prefetch_related("lines", "payments"), number=number, contact=contact)
    return render(request, "shiv_erp/portal_document_detail.html", {"contact": contact, "doc": doc})


@login_required(login_url="customer_login")
def portal_document_pdf(request: HttpRequest, number: str) -> HttpResponse:
    contact = _portal_contact(request)
    doc = get_object_or_404(Document.objects.prefetch_related("lines"), number=number, contact=contact)

    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    y = height - 50
    p.setFont("Helvetica-Bold", 14)
    p.drawString(40, y, f"{doc.get_doc_type_display()} - {doc.number}")
    y -= 20

    p.setFont("Helvetica", 10)
    p.drawString(40, y, f"Contact: {doc.contact.name}")
    y -= 15
    p.drawString(40, y, f"Date: {doc.issue_date}")
    y -= 15
    if doc.due_date:
        p.drawString(40, y, f"Due: {doc.due_date}")
        y -= 15
    p.drawString(40, y, f"Status: {doc.get_status_display()}")
    y -= 25

    p.setFont("Helvetica-Bold", 10)
    p.drawString(40, y, "Description")
    p.drawString(350, y, "Qty")
    p.drawString(420, y, "Unit")
    p.drawString(490, y, "Total")
    y -= 15
    p.setFont("Helvetica", 10)
    for line in doc.lines.all():
        if y < 80:
            p.showPage()
            y = height - 50
        desc = line.description or (line.product.name if line.product else "")
        p.drawString(40, y, desc[:55])
        p.drawRightString(395, y, f"{line.quantity}")
        p.drawRightString(470, y, f"{line.unit_price}")
        p.drawRightString(555, y, f"{line.line_total}")
        y -= 14

    y -= 10
    p.setFont("Helvetica-Bold", 12)
    p.drawRightString(555, y, f"Total: {doc.total_amount}")
    y -= 18
    if doc.is_financial:
        p.setFont("Helvetica", 10)
        p.drawRightString(555, y, f"Paid: {doc.paid_amount}")
        y -= 14
        p.drawRightString(555, y, f"Payment Status: {doc.get_payment_status_display()}")

    p.showPage()
    p.save()
    buffer.seek(0)
    filename = f"{doc.number}.pdf"
    return FileResponse(buffer, as_attachment=True, filename=filename)


@login_required(login_url="customer_login")
def portal_pay_document(request: HttpRequest, number: str) -> HttpResponse:
    contact = _portal_contact(request)
    doc = get_object_or_404(Document.objects.prefetch_related("payments"), number=number, contact=contact)
    if doc.doc_type != Document.Type.CUSTOMER_INVOICE:
        raise Http404()
    if doc.payment_status == Document.PaymentStatus.PAID:
        return redirect("portal_document_detail", number=doc.number)

    remaining = (doc.total_amount - doc.paid_amount).quantize(Decimal("0.01"))
    if request.method == "POST":
        form = PortalPaymentForm(request.POST)
        if form.is_valid():
            amount: Decimal = form.cleaned_data["amount"]
            method = form.cleaned_data["method"]
            if amount > remaining + Decimal("0.0001"):
                form.add_error("amount", "Amount cannot exceed remaining balance.")
            else:
                Payment.objects.create(
                    document=doc,
                    payment_date=timezone.localdate(),
                    method=method,
                    amount=amount,
                    status=Payment.Status.POSTED,
                )
                return redirect("portal_document_detail", number=doc.number)
    else:
        form = PortalPaymentForm(initial={"amount": remaining, "method": Payment.Method.ONLINE})

    return render(
        request,
        "shiv_erp/portal_pay.html",
        {"contact": contact, "doc": doc, "form": form, "remaining": remaining},
    )


@staff_required
def budget_report(request: HttpRequest) -> HttpResponse:
    period_id = request.GET.get("period")
    selected_kind = request.GET.get("kind") or ""

    periods = BudgetPeriod.objects.filter(is_active=True).order_by("-start_date")
    if not periods.exists():
        today = timezone.localdate()
        start_date = today.replace(day=1)
        end_date = today.replace(day=calendar.monthrange(today.year, today.month)[1])
        BudgetPeriod.objects.create(name=today.strftime("%b %Y"), start_date=start_date, end_date=end_date, is_active=True)
        periods = BudgetPeriod.objects.filter(is_active=True).order_by("-start_date")

    selected_period = periods.filter(id=period_id).first() if period_id else None
    default_period = periods.first()

    quick_entry_form = QuickEntryForm(
        initial={"period": selected_period or default_period, "kind": selected_kind or Budget.Kind.EXPENSE}
    )

    if request.method == "POST":
        quick_entry_form = QuickEntryForm(request.POST)
        if quick_entry_form.is_valid():
            period = quick_entry_form.cleaned_data["period"] or default_period
            kind = quick_entry_form.cleaned_data["kind"]
            cost_center_name = quick_entry_form.cleaned_data["cost_center_name"].strip()
            cost_center_code = (quick_entry_form.cleaned_data.get("cost_center_code") or "").strip()
            budget_amount = quick_entry_form.cleaned_data["budget_amount"]
            actual_amount = quick_entry_form.cleaned_data.get("actual_amount")

            with transaction.atomic():
                analytic = None
                if cost_center_code:
                    analytic = AnalyticalAccount.objects.filter(code=cost_center_code).first()
                if analytic is None:
                    analytic = AnalyticalAccount.objects.filter(name=cost_center_name).first()
                if analytic is None:
                    analytic = AnalyticalAccount.objects.create(name=cost_center_name, code=cost_center_code)

                budget, created = Budget.objects.get_or_create(
                    analytic_account=analytic,
                    period=period,
                    kind=kind,
                    defaults={"amount": budget_amount, "is_active": True},
                )
                if (not created) and budget.amount != budget_amount:
                    budget.amount = budget_amount
                    budget.save(update_fields=["amount", "updated_at"])
                    BudgetRevision.objects.create(budget=budget, revised_amount=budget_amount, revised_by=request.user)

                if actual_amount is not None and actual_amount > 0:
                    if kind == Budget.Kind.EXPENSE:
                        contact, _ = Contact.objects.get_or_create(
                            name="Demo Vendor",
                            defaults={"contact_type": Contact.Type.VENDOR, "is_active": True},
                        )
                        doc_type = Document.Type.VENDOR_BILL
                    else:
                        contact, _ = Contact.objects.get_or_create(
                            name="Demo Customer",
                            defaults={"contact_type": Contact.Type.CUSTOMER, "is_active": True},
                        )
                        doc_type = Document.Type.CUSTOMER_INVOICE

                    issue_date = period.start_date
                    doc = Document.objects.create(
                        doc_type=doc_type,
                        contact=contact,
                        issue_date=issue_date,
                        due_date=issue_date + timedelta(days=30),
                    )
                    DocumentLine.objects.create(
                        document=doc,
                        description="Quick entry",
                        quantity=Decimal("1.00"),
                        unit_price=actual_amount,
                        analytic_account=analytic,
                    )
                    doc.post()

            messages.success(request, "Entry saved. Dashboard updated.")
            qs = []
            if period_id:
                qs.append(f"period={period_id}")
            if selected_kind:
                qs.append(f"kind={selected_kind}")
            url = reverse("budget_report")
            if qs:
                url = f"{url}?{'&'.join(qs)}"
            return redirect(url)

    budgets = Budget.objects.filter(is_active=True).select_related("analytic_account", "period")
    if selected_period:
        budgets = budgets.filter(period=selected_period)
    if selected_kind in {Budget.Kind.EXPENSE, Budget.Kind.REVENUE}:
        budgets = budgets.filter(kind=selected_kind)

    rows = []
    labels = []
    actuals = []
    planned = []
    for b in budgets:
        labels.append(str(b.analytic_account))
        planned.append(float(b.amount))
        actuals.append(float(b.actual_amount))
        rows.append(
            {
                "budget": b,
                "actual": b.actual_amount,
                "variance": b.variance,
                "achievement": b.achievement_percent,
                "remaining": b.remaining_balance,
            }
        )

    def _pct(n: Decimal, d: Decimal) -> Decimal:
        if d <= 0:
            return Decimal("0.00")
        return (n / d * Decimal("100")).quantize(Decimal("0.01"))

    expense_budget_total = (
        budgets.filter(kind=Budget.Kind.EXPENSE).aggregate(total=Sum("amount")).get("total") or Decimal("0.00")
    ).quantize(Decimal("0.01"))
    revenue_budget_total = (
        budgets.filter(kind=Budget.Kind.REVENUE).aggregate(total=Sum("amount")).get("total") or Decimal("0.00")
    ).quantize(Decimal("0.01"))

    analytic_ids = list(budgets.values_list("analytic_account_id", flat=True).distinct())
    expense_actual_total = Decimal("0.00")
    revenue_actual_total = Decimal("0.00")
    if analytic_ids and selected_period:
        line_qs = DocumentLine.objects.filter(
            analytic_account_id__in=analytic_ids,
            document__status=Document.Status.POSTED,
            document__issue_date__gte=selected_period.start_date,
            document__issue_date__lte=selected_period.end_date,
        )
        expense_actual_total = (
            line_qs.filter(document__doc_type=Document.Type.VENDOR_BILL).aggregate(total=Sum("line_total")).get("total")
            or Decimal("0.00")
        ).quantize(Decimal("0.01"))
        revenue_actual_total = (
            line_qs.filter(document__doc_type=Document.Type.CUSTOMER_INVOICE)
            .aggregate(total=Sum("line_total"))
            .get("total")
            or Decimal("0.00")
        ).quantize(Decimal("0.01"))

    revenue_achievement_pct = _pct(revenue_actual_total, revenue_budget_total)
    expense_used_pct = _pct(expense_actual_total, expense_budget_total)
    expense_control_pct = (Decimal("100.00") - expense_used_pct).quantize(Decimal("0.01"))
    if expense_control_pct < 0:
        expense_control_pct = Decimal("0.00")
    if expense_control_pct > 100:
        expense_control_pct = Decimal("100.00")

    payment_qs = Payment.objects.filter(status=Payment.Status.POSTED).select_related("document")
    if selected_period:
        payment_qs = payment_qs.filter(
            payment_date__gte=selected_period.start_date,
            payment_date__lte=selected_period.end_date,
        )

    cash_received = (
        payment_qs.filter(document__doc_type=Document.Type.CUSTOMER_INVOICE).aggregate(total=Sum("amount")).get("total")
        or Decimal("0.00")
    ).quantize(Decimal("0.01"))
    cash_paid = (
        payment_qs.filter(document__doc_type=Document.Type.VENDOR_BILL).aggregate(total=Sum("amount")).get("total")
        or Decimal("0.00")
    ).quantize(Decimal("0.01"))
    cash_net = (cash_received - cash_paid).quantize(Decimal("0.01"))

    cash_operating_change_pct = None
    if selected_period:
        period_days = (selected_period.end_date - selected_period.start_date).days + 1
        prev_end = selected_period.start_date - timedelta(days=1)
        prev_start = prev_end - timedelta(days=period_days - 1)

        prev_payment_qs = Payment.objects.filter(
            status=Payment.Status.POSTED,
            payment_date__gte=prev_start,
            payment_date__lte=prev_end,
        ).select_related("document")

        prev_received = (
            prev_payment_qs.filter(document__doc_type=Document.Type.CUSTOMER_INVOICE)
            .aggregate(total=Sum("amount"))
            .get("total")
            or Decimal("0.00")
        )
        prev_paid = (
            prev_payment_qs.filter(document__doc_type=Document.Type.VENDOR_BILL).aggregate(total=Sum("amount")).get("total")
            or Decimal("0.00")
        )
        prev_net = prev_received - prev_paid
        if prev_net != 0:
            cash_operating_change_pct = ((cash_net - prev_net) / abs(prev_net) * Decimal("100")).quantize(Decimal("0.01"))

    docs_qs = Document.objects.filter(
        status=Document.Status.POSTED,
        doc_type__in=[Document.Type.VENDOR_BILL, Document.Type.CUSTOMER_INVOICE],
        due_date__isnull=False,
    )
    if selected_period:
        docs_qs = docs_qs.filter(issue_date__gte=selected_period.start_date, issue_date__lte=selected_period.end_date)

    docs_qs = docs_qs.annotate(
        last_payment=Max("payments__payment_date", filter=Q(payments__status=Payment.Status.POSTED))
    )

    docs_due_total = docs_qs.count()
    docs_on_time = docs_qs.filter(payment_status=Document.PaymentStatus.PAID, last_payment__lte=F("due_date")).count()
    payment_health_pct = Decimal("0.00")
    if docs_due_total:
        payment_health_pct = (Decimal(docs_on_time) / Decimal(docs_due_total) * Decimal("100")).quantize(Decimal("0.01"))

    overall_score = int(
        round(float((revenue_achievement_pct + expense_control_pct + payment_health_pct) / Decimal("3.00")))
    )
    if overall_score < 0:
        overall_score = 0
    if overall_score > 100:
        overall_score = 100

    alerts: list[dict[str, str]] = []
    today = timezone.localdate()
    overdue_count = (
        Document.objects.filter(
            status=Document.Status.POSTED,
            doc_type__in=[Document.Type.VENDOR_BILL, Document.Type.CUSTOMER_INVOICE],
            due_date__lt=today,
        )
        .exclude(payment_status=Document.PaymentStatus.PAID)
        .count()
    )
    if overdue_count:
        alerts.append(
            {
                "title": f"{overdue_count} overdue invoice/bill(s) pending payment",
                "subtitle": "Follow up and record payments to keep books accurate",
                "url": reverse("admin:shiv_erp_document_changelist"),
            }
        )

    over_budget_count = 0
    for b in budgets.filter(kind=Budget.Kind.EXPENSE)[:50]:
        if b.amount > 0 and b.actual_amount > b.amount:
            over_budget_count += 1
            if len(alerts) < 3:
                alerts.append(
                    {
                        "title": f"{b.analytic_account} is over budget",
                        "subtitle": f"Budget {b.amount} vs Actual {b.actual_amount}",
                        "url": reverse("budget_report"),
                    }
                )

    if over_budget_count and len(alerts) < 4:
        alerts.append(
            {
                "title": f"{over_budget_count} cost center(s) are over budget",
                "subtitle": "Review posted bills and revise budgets if needed",
                "url": reverse("budget_report"),
            }
        )

    return render(
        request,
        "shiv_erp/budget_report.html",
        {
            "periods": periods,
            "selected_period": selected_period,
            "selected_kind": selected_kind,
            "rows": rows,
            "chart_labels": labels,
            "chart_planned": planned,
            "chart_actuals": actuals,
            "kinds": Budget.Kind.choices,
            "overall_score": overall_score,
            "revenue_achievement_pct": revenue_achievement_pct,
            "expense_control_pct": expense_control_pct,
            "cash_received": cash_received,
            "cash_paid": cash_paid,
            "cash_net": cash_net,
            "cash_operating": cash_net,
            "cash_operating_change_pct": cash_operating_change_pct,
            "payment_health_pct": payment_health_pct,
            "alerts": alerts,
            "quick_entry_form": quick_entry_form,
        },
    )


def home_redirect(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        if request.user.is_staff:
            return redirect(reverse("budget_report"))
        return redirect(reverse("portal_dashboard"))
    return redirect(reverse("login"))
